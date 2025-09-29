import asyncio
# cogs/forum_tools.py
import discord
from discord.ext import commands
from discord import app_commands
from discord.ext import tasks
import os
import sqlite3
from typing import Optional
import datetime
from dotenv import set_key, unset_key
from .random_post import create_gacha_panel
import json

# --- 数据库文件路径 ---
DB_FILE = 'posts.db'

# --- Cog 类 ---
class ForumTools(commands.Cog):
    """
    处理与论坛频道相关的功能，包括新帖速递、后台同步和手动同步。
    配置现在完全由 .env 文件驱动。
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # --- 从 .env 读取轮询间隔, 默认为 2 小时 ---
        try:
            sync_hours = float(os.getenv("SYNC_INTERVAL_HOURS", "2.0"))
        except ValueError:
            print("⚠️ SYNC_INTERVAL_HOURS 值无效，将使用默认值 2 小时。")
            sync_hours = 2.0
        
        # 动态修改任务的循环间隔并启动
        self.incremental_sync_task.change_interval(hours=sync_hours)
        
        # 启动新的清理任务
        self.cleanup_old_posts_task.start()

    def cog_unload(self):
        self.incremental_sync_task.cancel()
        self.cleanup_old_posts_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        """当Cog加载且Bot准备就绪后，安全地启动后台任务。"""
        if not self.incremental_sync_task.is_running():
            print("[ForumTools] Bot is ready, starting incremental_sync_task.")
            self.incremental_sync_task.start()
        if not self.cleanup_old_posts_task.is_running():
            print("[ForumTools] Bot is ready, starting cleanup_old_posts_task.")
            self.cleanup_old_posts_task.start()

    # 移除这里的硬编码时间, 在 __init__ 中动态设置
    @tasks.loop()
    async def incremental_sync_task(self):
        """后台增量同步任务，只获取上次同步后产生的新帖子。"""
        await self.bot.wait_until_ready()
        print("\n" + "="*50)
        print("[后台任务] 开始执行增量同步...")
        
        # 直接从 bot 实例获取监控频道列表
        forum_ids_to_scan = self.bot.allowed_forum_ids
        print(f"[后台任务] 本次将要扫描的频道ID列表: {list(forum_ids_to_scan)}")

        if not forum_ids_to_scan:
            print("[后台任务] 未配置任何监控频道，跳过增量同步。")
            print("="*50 + "\n")
            return

        def _get_last_id_from_db(forum_id):
            con = sqlite3.connect(DB_FILE)
            cur = con.cursor()
            cur.execute("SELECT MAX(thread_id) FROM threads WHERE forum_id = ?", (forum_id,))
            row = cur.fetchone()
            con.close()
            return row[0] if row and row[0] else None

        def _insert_threads_to_db(thread_data):
            if not thread_data:
                return 0
            con = sqlite3.connect(DB_FILE)
            cur = con.cursor()
            cur.executemany("INSERT OR IGNORE INTO threads (thread_id, forum_id, guild_id) VALUES (?, ?, ?)", thread_data)
            row_count = cur.rowcount
            con.commit()
            con.close()
            return row_count

        total_added = 0
        for forum_id in forum_ids_to_scan:
            try:
                forum = self.bot.get_channel(forum_id) or await self.bot.fetch_channel(forum_id)
                if not forum or not isinstance(forum, discord.ForumChannel):
                    print(f"[后台任务] 找不到或无效的论坛频道ID: {forum_id}，从列表跳过。")
                    continue
                
                print(f"[后台任务] ==> 正在处理频道: {forum.name} (ID: {forum_id})")
                
                last_id = await asyncio.to_thread(_get_last_id_from_db, forum_id)

                if last_id is None:
                    print(f"[后台任务] 论坛 '{forum.name}' 在数据库中为空，跳过。等待手动全量同步。")
                    continue

                new_threads = []
                for thread in forum.threads:
                    if thread.id > last_id:
                        new_threads.append(thread)
                async for thread in forum.archived_threads(limit=None):
                    if thread.id > last_id:
                        new_threads.append(thread)

                if new_threads:
                    unique_new_threads = {t.id: t for t in new_threads}.values()
                    thread_data = [(t.id, forum.id, forum.guild.id) for t in unique_new_threads]
                    added_count = await asyncio.to_thread(_insert_threads_to_db, thread_data)
                    total_added += added_count

            except discord.Forbidden:
                print(f"[后台任务] 权限不足，无法增量同步论坛 (ID: {forum_id})。")
            except Exception as e:
                print(f"[后台任务] 增量同步论坛 (ID: {forum_id}) 时出错: {type(e).__name__}: {e}")
        
        if total_added > 0:
            print(f"[后台任务] 增量同步完成。本次新增了 {total_added} 个帖子。")
        else:
            print("[后台任务] 增量同步完成。没有新帖子。")
        print("="*50 + "\n")

    # --- 事件监听器：当新帖子创建时 ---
    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        """
        当在任何被监控的论坛频道中创建新帖子时触发。
        同时处理新帖速递和数据库更新。
        """
        forum_id = thread.parent_id
        def log_with_timestamp(message):
            """一个简单的日志记录函数，自动添加时间戳。"""
            print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

        log_with_timestamp(f"[新帖监听] 检测到新帖子 '{thread.name}' (ID: {thread.id}) 在频道 '{thread.parent.name}' (ID: {forum_id}) 中创建。")

        # --- 检查帖子来源是否在监控且未被排除的频道列表中 ---
        # 1. 必须在总的监控列表里
        if forum_id not in self.bot.allowed_forum_ids:
            log_with_timestamp(f"[新帖监听] 忽略：帖子源频道 '{thread.parent.name}' 不在 .env 配置的 ALLOWED_CHANNEL_IDS 监控列表中。")
            return
        
        # 2. 不能在排除列表里
        if forum_id in self.bot.default_pool_exclusions:
            log_with_timestamp(f"[新帖监听] 忽略：帖子源频道 '{thread.parent.name}' 在 .env 配置的 DEFAULT_POOL_EXCLUSIONS 排除列表中，因此不进行速递。")
            return

        # 1. 更新数据库
        def _update_db(thread_id, forum_id, guild_id):
            try:
                con = sqlite3.connect(DB_FILE)
                cur = con.cursor()
                cur.execute(
                    "INSERT OR IGNORE INTO threads (thread_id, forum_id, guild_id) VALUES (?, ?, ?)",
                    (thread_id, forum_id, guild_id)
                )
                con.commit()
                con.close()
            except Exception as e:
                log_with_timestamp(f"数据库错误 (on_thread_create): {e}")

        await asyncio.to_thread(_update_db, thread.id, forum_id, thread.guild.id)

        # 2. 处理新帖速递
        # 2. 异步处理新帖速递
        # 创建一个后台任务来处理，这样 on_thread_create 不会被长时间阻塞
        asyncio.create_task(self._send_delivery_with_retries(thread))

    async def _send_delivery_with_retries(self, thread: discord.Thread):
        """
        一个独立的、带重试逻辑的异步任务，用于构建和发送新帖速递。
        每次重试都会从头开始构建 Embed。
        """
        # --- 从 .env 加载速递相关配置, 提供默认值 ---
        try:
            fetch_delay = float(os.getenv("FETCH_STARTER_MESSAGE_DELAY_SECONDS", "15.0"))
            send_max_attempts = int(os.getenv("DELIVERY_MAX_RETRIES", "5"))
            send_retry_delay = float(os.getenv("DELIVERY_RETRY_DELAY_SECONDS", "60.0"))
        except ValueError:
            print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [新帖速递] ⚠️ .env 文件中的速递配置值无效，将使用默认值。")
            fetch_delay = 15.0
            send_max_attempts = 5
            send_retry_delay = 60.0

        delivery_channel_id = self.bot.delivery_channel_id
        if not delivery_channel_id:
            return
        
        delivery_channel = self.bot.get_channel(delivery_channel_id)
        if not delivery_channel:
            print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [新帖速递] ❌ 错误：在 .env 中配置的速递频道ID {delivery_channel_id} 找不到。")
            return

        # --- 首次尝试前的初始延迟 ---
        print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [新帖速递] 检测到新帖 '{thread.name}'。等待 {fetch_delay} 秒，以确保CDN资源就绪...")
        await asyncio.sleep(fetch_delay)

        for attempt in range(send_max_attempts):
            try:
                print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [新帖速递] 正在为 '{thread.name}' 进行第 {attempt + 1}/{send_max_attempts} 次构建和发送尝试...")
                
                # --- 步骤 1: 在每次循环内部获取起始消息 ---
                starter_message = None
                try:
                    # 使用更短的超时来快速失败
                    starter_message = await asyncio.wait_for(thread.fetch_message(thread.id), timeout=10.0)
                except (discord.NotFound, asyncio.TimeoutError):
                    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [新帖速递] 注意：在第 {attempt + 1} 次尝试中未能获取到帖子 '{thread.name}' 的起始消息。")
                    # 即使没有消息，我们仍然可以发送一个不带内容的速递
                    pass
                except discord.Forbidden:
                    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [新帖速递] ❌ 失败：机器人权限不足，无法获取帖子 '{thread.name}' 的起始消息。已终止对此帖的速递。")
                    return # 权限问题无法通过重试解决，直接返回

                # --- 步骤 2: 在每次循环内部构建 Embed ---
                author_mention = f"**👤 作者:** {thread.owner.name}" if thread.owner else f"**👤 作者:** 未知"
                thread_title = thread.name[:97] + "..." if len(thread.name) > 100 else thread.name
                header_line = f"**{thread_title}** | {author_mention}"

                if starter_message and starter_message.content:
                    post_content = starter_message.content
                    if len(post_content) > 400:
                        post_content = post_content[:400] + "..."
                    content_section = f"**📝 内容速览:**\n{post_content}"
                else:
                    content_section = "**📝 内容速览:**\n*(无法加载起始消息，可能已被删除或帖子格式特殊)*"
                
                full_description = f"{header_line}\n\n{content_section}"
                embed = discord.Embed(title="✨ 新卡速递", description=full_description, color=discord.Color.blue())
                embed.add_field(name="🚪 传送门", value=f"[点击查看原帖]({thread.jump_url})", inline=False)

                if starter_message and starter_message.attachments:
                    for attachment in starter_message.attachments:
                        if attachment.content_type and attachment.content_type.startswith('image/'):
                            embed.set_thumbnail(url=attachment.url)
                            break
                
                if thread.applied_tags:
                    tags_str = ", ".join(tag.name for tag in thread.applied_tags)
                    if len(tags_str) > 1024:
                        tags_str = tags_str[:1021] + "..."
                    embed.add_field(name="🏷️ 标签", value=tags_str, inline=False)

                print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [诊断日志] 准备为帖子 '{thread.name}' (ID: {thread.id}) 发送以下 Embed 内容:\n{embed.to_dict()}")

                # --- 步骤 3: 发送 Embed ---
                sent_message = await delivery_channel.send(embed=embed)

                # --- 步骤 4: 验证 ---
                if sent_message and sent_message.embeds:
                    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [新帖速递] ✅ 第 {attempt + 1} 次尝试成功！消息 (ID: {sent_message.id}) 已成功发送。")
                    
                    # --- 成功后，异步执行面板重建 ---
                    async def rebuild_panel():
                        await asyncio.sleep(2) # 战略性延迟
                        try:
                            # 查找并删除旧面板
                            async for message in delivery_channel.history(limit=100):
                                if message.author == self.bot.user and message.embeds and message.embeds[0].title == "🎉 类脑抽抽乐 🎉":
                                    await message.delete()
                                    break
                            # 创建新面板
                            await create_gacha_panel(self.bot, delivery_channel)
                            print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [面板管理] 抽卡面板已成功为帖子 '{thread.name}' 重建。")
                        except Exception as e:
                            print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [面板管理] 严重错误：为帖子 '{thread.name}' 重建抽卡面板时失败: {e}")
                    
                    asyncio.create_task(rebuild_panel())
                    return # 任务完成，退出函数

                else:
                    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [新帖速递] ⚠️ 第 {attempt + 1} 次尝试失败：API返回了空消息或无效消息对象。将在 {send_retry_delay} 秒后重试...")

            except discord.HTTPException as e:
                print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [新帖速递] ⚠️ 第 {attempt + 1} 次尝试失败：遇到HTTP异常 {e.status} (Code: {e.code})。将在 {send_retry_delay} 秒后重试...")
            except Exception as e:
                import traceback
                print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [新帖速递] ❌ 第 {attempt + 1} 次尝试时遇到严重未知错误: {type(e).__name__}: {e}。")
                print(f"Traceback: {traceback.format_exc()}")
                # 遇到未知错误，可能重试也无用，直接终止
                break
            
            # 如果还未成功，且不是最后一次尝试，则等待
            if attempt < send_max_attempts - 1:
                await asyncio.sleep(send_retry_delay)

        # 如果循环完成所有次数都未成功
        print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [新帖速递] ❌ 最终失败：在 {send_max_attempts} 次尝试后，仍未能成功发送关于帖子 '{thread.name}' 的速递。")


    @tasks.loop(hours=1)
    async def cleanup_old_posts_task(self):
        """后台任务，每小时运行一次，清理超过24小时的速递消息。"""
        await self.bot.wait_until_ready()
        
        delivery_channel_id = self.bot.delivery_channel_id
        if not delivery_channel_id:
            return # 如果没有设置速递频道，则不执行任何操作

        channel = self.bot.get_channel(delivery_channel_id)
        if not channel:
            return

        # print(f"[清理任务] 开始检查频道 '{channel.name}' 中的旧帖子...") # 注释掉，以减少不必要的日志
        deleted_count = 0
        
        # 计算24小时前的时间点
        time_limit = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)

        try:
            async for message in channel.history(limit=None, oldest_first=True):
                # 如果消息比时间限制还早，就处理它
                if message.created_at < time_limit:
                    # --- 规则1: 删除带有 "新卡速递" embed 的旧机器人消息 ---
                    if message.author == self.bot.user and message.embeds:
                        if message.embeds[0].title and "新卡速递" in message.embeds[0].title:
                            try:
                                await message.delete()
                                deleted_count += 1
                                await asyncio.sleep(1) # 增加延迟避免速率限制
                            except discord.Forbidden:
                                print(f"[清理任务] 权限不足，无法删除消息 {message.id}。")
                                break
                            except discord.HTTPException as e:
                                print(f"[清理任务] 删除消息 {message.id} 时出错: {e}")
                    # --- 规则2: 删除由机器人发送的、完全为空的旧消息 ---
                    elif message.author == self.bot.user and not message.embeds and not message.content:
                        try:
                            await message.delete()
                            deleted_count += 1
                            print(f"[清理任务] 发现并删除了一条旧的空消息 (ID: {message.id})。")
                            await asyncio.sleep(1) # 增加延迟避免速率限制
                        except discord.Forbidden:
                            print(f"[清理任务] 权限不足，无法删除空消息 {message.id}。")
                            break
                        except discord.HTTPException as e:
                            print(f"[清理任务] 删除空消息 {message.id} 时出错: {e}")
                else:
                    # 因为我们从最旧的消息开始，一旦遇到一个在24小时内的消息，
                    # 就可以确定后面的所有消息都是新的，无需再检查
                    break
        except discord.Forbidden:
            print(f"[清理任务] 权限不足，无法读取频道 '{channel.name}' 的历史记录。")
        except Exception as e:
            print(f"[清理任务] 发生未知错误: {e}")

        if deleted_count > 0:
            print(f"[清理任务] 清理完成，在频道 '{channel.name}' 中成功删除了 {deleted_count} 条超过24小时的旧速递。")

    # --- 斜杠命令组：/设置 ---
    # 移除了所有动态配置命令，现在只保留手动同步
    config_group = app_commands.Group(name="设置", description="机器人设置与管理", guild_only=True)

    @config_group.command(name="手动全量同步", description="【重要】将.env中配置的论坛所有帖子同步到数据库。")
    async def full_sync_command(self, interaction: discord.Interaction):
        """手动执行一次全量同步，获取所有活跃和归档的帖子。"""
        await interaction.response.defer(ephemeral=True, thinking=True)

        # --- 从 .env 加载管理员配置 ---
        admin_role_ids_str = os.getenv("ADMIN_ROLE_IDS", "")
        if not admin_role_ids_str:
            await interaction.followup.send("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `ADMIN_ROLE_IDS`。", ephemeral=True)
            return
        admin_role_ids = {int(rid.strip()) for rid in admin_role_ids_str.split(',')}

        # --- 权限检查 ---
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(admin_role_ids):
            await interaction.followup.send("🚫 **权限不足**：只有拥有特定管理员身份组的用户才能执行此操作。", ephemeral=True)
            return

        # --- 从 bot 实例获取监控频道列表 ---
        forum_ids_to_scan = self.bot.allowed_forum_ids
        if not forum_ids_to_scan:
            await interaction.followup.send("❌ **配置错误**：机器人尚未在 `.env` 文件中配置 `ALLOWED_CHANNEL_IDS`。", ephemeral=True)
            return

        # --- 异步收集数据 ---
        all_thread_data = []
        guild = interaction.guild
        for forum_id in forum_ids_to_scan:
            forum = guild.get_channel(forum_id)
            if not forum or not isinstance(forum, discord.ForumChannel):
                continue
            try:
                active_threads = forum.threads
                archived_threads = [t async for t in forum.archived_threads(limit=None)]
                
                for thread in active_threads + archived_threads:
                    all_thread_data.append((thread.id, forum.id, guild.id))
            except discord.Forbidden:
                print(f"[手动同步] 权限警告：无法同步论坛 {forum.mention} 的归档帖子。")
            except Exception as e:
                print(f"[手动同步] 收集论坛 '{forum.name}' 数据时出错: {e}")

        # --- 同步写入数据库 ---
        def _write_to_db(data):
            if not data:
                return 0
            con = sqlite3.connect(DB_FILE)
            cur = con.cursor()
            cur.executemany("INSERT OR IGNORE INTO threads (thread_id, forum_id, guild_id) VALUES (?, ?, ?)", data)
            added_count = cur.rowcount
            con.commit()
            con.close()
            return added_count

        total_added = await asyncio.to_thread(_write_to_db, all_thread_data)
        
        await interaction.followup.send(f"✅ **全量同步完成！** 本次新增了 **{total_added}** 个帖子到总卡池中。", ephemeral=True)

    @config_group.command(name="设置速递频道", description="【重要】设置或更新新帖速递的目标频道。")
    @app_commands.describe(channel="要设置为速递目标的文本频道")
    async def set_delivery_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """处理设置速递频道的命令。"""
        # --- 权限检查 (复用 ADMIN_ROLE_IDS) ---
        admin_role_ids_str = os.getenv("ADMIN_ROLE_IDS", "")
        if not admin_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `ADMIN_ROLE_IDS`。", ephemeral=True)
            return
        admin_role_ids = {int(rid.strip()) for rid in admin_role_ids_str.split(',')}
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(admin_role_ids):
            await interaction.response.send_message("🚫 **权限不足**：只有拥有特定管理员身份组的用户才能执行此操作。", ephemeral=True)
            return
        
        try:
            dotenv_path = os.path.join(os.getcwd(), '.env')
            await asyncio.to_thread(set_key, dotenv_path, "DELIVERY_CHANNEL_ID", str(channel.id))
            
            self.bot.delivery_channel_id = channel.id
            
            await interaction.response.send_message(
                f"✅ **成功!** 新帖速递频道已更新为 {channel.mention}。\n"
                f"**重要提示**: 此更改已写入 `.env` 文件，但为了确保所有功能完全同步，建议您在方便时**重启机器人**。",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ **写入 .env 文件失败**: `{e}`", ephemeral=True)

    @config_group.command(name="移除速递频道", description="【重要】禁用新帖速递功能。")
    async def unset_delivery_channel(self, interaction: discord.Interaction):
        """处理移除速递频道的命令。"""
        # --- 权限检查 (复用 ADMIN_ROLE_IDS) ---
        admin_role_ids_str = os.getenv("ADMIN_ROLE_IDS", "")
        if not admin_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `ADMIN_ROLE_IDS`。", ephemeral=True)
            return
        admin_role_ids = {int(rid.strip()) for rid in admin_role_ids_str.split(',')}
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(admin_role_ids):
            await interaction.response.send_message("🚫 **权限不足**：只有拥有特定管理员身份组的用户才能执行此操作。", ephemeral=True)
            return

        try:
            dotenv_path = os.path.join(os.getcwd(), '.env')
            await asyncio.to_thread(unset_key, dotenv_path, "DELIVERY_CHANNEL_ID")

            self.bot.delivery_channel_id = None

            await interaction.response.send_message(
                f"✅ **成功!** 已禁用新帖速递功能。\n"
                f"**重要提示**: 此更改已写入 `.env` 文件，但为了确保所有功能完全同步，建议您在方便时**重启机器人**。",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ **写入 .env 文件失败**: `{e}`", ephemeral=True)

    @config_group.command(name="添加监控论坛", description="【重要】添加一个新的论坛频道到监控列表。")
    @app_commands.describe(channel="要添加的论坛频道")
    async def add_monitored_forum(self, interaction: discord.Interaction, channel: discord.ForumChannel):
        """处理添加监控论坛的命令。"""
        # --- 权限检查 ---
        admin_role_ids_str = os.getenv("ADMIN_ROLE_IDS", "")
        if not admin_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `ADMIN_ROLE_IDS`。", ephemeral=True)
            return
        admin_role_ids = {int(rid.strip()) for rid in admin_role_ids_str.split(',')}
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(admin_role_ids):
            await interaction.response.send_message("🚫 **权限不足**。", ephemeral=True)
            return

        try:
            def _update_env():
                dotenv_path = os.path.join(os.getcwd(), '.env')
                current_ids_str = os.getenv("ALLOWED_CHANNEL_IDS", "")
                current_ids = {cid.strip() for cid in current_ids_str.split(',') if cid.strip()}
                current_ids.add(str(channel.id))
                new_ids_str = ",".join(current_ids)
                set_key(dotenv_path, "ALLOWED_CHANNEL_IDS", new_ids_str)
                return {int(cid) for cid in current_ids}

            updated_ids = await asyncio.to_thread(_update_env)
            self.bot.allowed_forum_ids = updated_ids

            await interaction.response.send_message(
                f"✅ **成功!** 已将论坛频道 {channel.mention} 添加到监控列表。\n"
                f"**重要提示**: 建议在方便时**重启机器人**以确保所有后台任务都使用最新配置。",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ **写入 .env 文件失败**: `{e}`", ephemeral=True)

    @config_group.command(name="移除监控论坛", description="【重要】从监控列表中移除一个论坛频道。")
    @app_commands.describe(channel="要移除的论坛频道")
    async def remove_monitored_forum(self, interaction: discord.Interaction, channel: discord.ForumChannel):
        """处理移除监控论坛的命令。"""
        # --- 权限检查 ---
        admin_role_ids_str = os.getenv("ADMIN_ROLE_IDS", "")
        if not admin_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `ADMIN_ROLE_IDS`。", ephemeral=True)
            return
        admin_role_ids = {int(rid.strip()) for rid in admin_role_ids_str.split(',')}
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(admin_role_ids):
            await interaction.response.send_message("🚫 **权限不足**。", ephemeral=True)
            return

        try:
            def _update_env():
                dotenv_path = os.path.join(os.getcwd(), '.env')
                current_ids_str = os.getenv("ALLOWED_CHANNEL_IDS", "")
                current_ids = {cid.strip() for cid in current_ids_str.split(',') if cid.strip()}
                current_ids.discard(str(channel.id))
                new_ids_str = ",".join(current_ids)
                set_key(dotenv_path, "ALLOWED_CHANNEL_IDS", new_ids_str)
                # Handle case where new_ids_str is empty
                return {int(cid) for cid in current_ids} if current_ids else set()

            updated_ids = await asyncio.to_thread(_update_env)
            self.bot.allowed_forum_ids = updated_ids

            await interaction.response.send_message(
                f"✅ **成功!** 已将论坛频道 {channel.mention} 从监控列表中移除。\n"
                f"**重要提示**: 建议在方便时**重启机器人**以确保所有后台任务都使用最新配置。",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ **写入 .env 文件失败**: `{e}`", ephemeral=True)


# --- Cog 设置函数 ---
async def setup(bot: commands.Bot):
    # Cog的加载会自动注册其中定义的所有命令组
    await bot.add_cog(ForumTools(bot))
