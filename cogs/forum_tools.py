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

        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        total_added = 0
        
        # 我们需要一个 guild 对象，但由于频道可能分散在不同服务器，
        # 我们将通过频道对象来获取 guild
        for forum_id in forum_ids_to_scan:
            try:
                forum = self.bot.get_channel(forum_id) or await self.bot.fetch_channel(forum_id)
                if not forum or not isinstance(forum, discord.ForumChannel):
                    print(f"[后台任务] 找不到或无效的论坛频道ID: {forum_id}，从列表跳过。")
                    continue
                
                print(f"[后台任务] ==> 正在处理频道: {forum.name} (ID: {forum_id})")
                guild = forum.guild

                cur.execute("SELECT MAX(thread_id) FROM threads WHERE forum_id = ?", (forum_id,))
                row = cur.fetchone()
                last_id = row[0] if row else None

                # 如果数据库中没有该论坛的记录，则跳过增量同步
                if last_id is None:
                    print(f"[后台任务] 论坛 '{forum.name}' 在数据库中为空，跳过。等待手动全量同步。")
                    continue

                # 高效地只获取比 last_id 新的帖子
                # 我们需要同时检查活跃和归档的帖子
                new_threads = []
                
                # 检查活跃帖子
                for thread in forum.threads:
                    if thread.id > last_id:
                        new_threads.append(thread)

                # 检查归档帖子 (该方法不支持 'after' 参数, 我们在内存中过滤)
                async for thread in forum.archived_threads(limit=None):
                    if thread.id > last_id:
                        new_threads.append(thread)

                if new_threads:
                    # 去重，以防万一有帖子在活跃和归档中同时出现
                    unique_new_threads = {t.id: t for t in new_threads}.values()
                    thread_data = [(t.id, forum.id, guild.id) for t in unique_new_threads]
                    cur.executemany("INSERT OR IGNORE INTO threads (thread_id, forum_id, guild_id) VALUES (?, ?, ?)", thread_data)
                    total_added += cur.rowcount

            except discord.Forbidden:
                print(f"[后台任务] 权限不足，无法增量同步论坛 '{forum.name}' (ID: {forum_id})。")
            except Exception as e:
                # 确保即使 forum 对象获取失败，我们也能知道是哪个ID出错了
                forum_name_for_log = f"'{forum.name}' " if 'forum' in locals() and forum else ""
                print(f"[后台任务] 增量同步论坛 {forum_name_for_log}(ID: {forum_id}) 时出错: {type(e).__name__}: {e}")
        
        con.commit()
        con.close()
        
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
        print(f"[新帖监听] 检测到新帖子 '{thread.name}' (ID: {thread.id}) 在频道 '{thread.parent.name}' (ID: {forum_id}) 中创建。")

        # --- 检查帖子来源是否在监控且未被排除的频道列表中 ---
        # 1. 必须在总的监控列表里
        if forum_id not in self.bot.allowed_forum_ids:
            print(f"[新帖监听] 忽略：帖子源频道 '{thread.parent.name}' 不在 .env 配置的 ALLOWED_CHANNEL_IDS 监控列表中。")
            return
        
        # 2. 不能在排除列表里
        if forum_id in self.bot.default_pool_exclusions:
            print(f"[新帖监听] 忽略：帖子源频道 '{thread.parent.name}' 在 .env 配置的 DEFAULT_POOL_EXCLUSIONS 排除列表中，因此不进行速递。")
            return

        # 1. 更新数据库
        try:
            con = sqlite3.connect(DB_FILE)
            cur = con.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO threads (thread_id, forum_id, guild_id) VALUES (?, ?, ?)",
                (thread.id, forum_id, thread.guild.id)
            )
            con.commit()
            con.close()
        except Exception as e:
            print(f"数据库错误 (on_thread_create): {e}")

        # 2. 处理新帖速递
        delivery_channel_id = self.bot.delivery_channel_id
        if not delivery_channel_id:
            return
        
        delivery_channel = self.bot.get_channel(delivery_channel_id)
        if not delivery_channel:
            # 仅在第一次找不到时打印一次警告，避免刷屏
            if not hasattr(self, '_delivery_channel_warning_sent'):
                print(f"错误：在 .env 中配置的速递频道ID {delivery_channel_id} 找不到。")
                self._delivery_channel_warning_sent = True
            return

        # --- 步骤 2a: 构造并发送速递消息 ---
        try:
            starter_message = None
            # --- 引入重试机制来获取起始消息，以应对 Discord API 的最终一致性延迟 ---
            max_retries = 2
            retry_delay = 2 # 秒
            for attempt in range(max_retries):
                try:
                    starter_message = thread.starter_message or await thread.fetch_message(thread.id)
                    # 如果是在重试后成功的，就打印一条成功日志
                    if attempt > 0:
                        print(f"[新帖速递] 信息：在第 {attempt + 1} 次尝试后，成功获取到帖子 '{thread.name}' 的起始消息。")
                    # 如果成功获取，就跳出循环
                    break
                except discord.NotFound:
                    if attempt < max_retries - 1:
                        print(f"[新帖速递] 注意：尝试第 {attempt + 1} 次获取帖子 '{thread.name}' 的起始消息失败 (NotFound)。将在 {retry_delay} 秒后重试...")
                        await asyncio.sleep(retry_delay)
                    else:
                        # 这是最后一次尝试，仍然失败
                        print(f"[新帖速递] 失败：在 {max_retries} 次尝试后，仍无法获取帖子 '{thread.name}' 的起始消息 (NotFound)。已跳过本次速递。")
                        return
                except discord.Forbidden as e:
                    # 如果是权限问题，重试没有意义，直接放弃
                    print(f"[新帖速递] 失败：无法获取帖子 '{thread.name}' 的起始消息，因为机器人权限不足。已跳过本次速递。原因: {e}")
                    return
                except Exception as e:
                    # 捕获其他可能的未知错误
                    print(f"[新帖速递] 失败：获取帖子 '{thread.name}' 的起始消息时发生未知错误。已跳过本次速递。原因: {e}")
                    return

            author_mention = f"**👤 作者:** {thread.owner.name}" if thread.owner else f"**👤 作者:** 未知"
            header_line = f"**{thread.name}** | {author_mention}"

            if starter_message:
                post_content = starter_message.content
                if len(post_content) > 400:
                    post_content = post_content[:400] + "..."
                content_section = f"**📝 内容速览:**\n{post_content}"
            else:
                content_section = "**📝 内容速览:**\n*(无法加载起始消息，可能已被删除或帖子格式特殊)*"
            
            full_description = f"{header_line}\n\n{content_section}"
            embed = discord.Embed(title="✨ 新卡速递", description=full_description, color=discord.Color.blue())
            embed.add_field(name="🚪 传送门", value=f"[点击查看原帖]({thread.jump_url})", inline=False)

            # 只有在 starter_message 存在时，才检查附件
            if starter_message and starter_message.attachments:
                for attachment in starter_message.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        embed.set_thumbnail(url=attachment.url)
                        break
            
            if thread.applied_tags:
                tags_str = ", ".join(tag.name for tag in thread.applied_tags)
                embed.add_field(name="🏷️ 标签", value=tags_str, inline=False)
            
            await delivery_channel.send(embed=embed)
            print(f"[新帖速递] ✅ 成功发送了关于帖子 '{thread.name}' 的速递到频道 '{delivery_channel.name}'。")

        except discord.errors.Forbidden:
            print(f"[新帖速递] 权限错误：机器人没有权限在频道 '{delivery_channel.name}' 中发送消息。")
            return # 无法发送速递，后续操作也无法进行，直接返回
        except Exception as e:
            print(f"[新帖速递] 失败：在发送速递消息时发生未知错误: {e}")
            return # 发送速递失败，后续操作也无法进行，直接返回

        # --- 步骤 2b: 重建抽卡面板 ---
        try:
            # 1. 查找并删除此频道中任何现有的抽卡面板
            async for message in delivery_channel.history(limit=100):
                if message.author == self.bot.user and message.embeds:
                    if message.embeds[0].title == "🎉 类脑抽抽乐 🎉":
                        try:
                            await message.delete()
                        except discord.HTTPException as e:
                            # 这个错误通常是 404 Not Found，意味着面板已被删除，可以安全地忽略
                            print(f"[面板管理] 删除旧面板时出现小问题 (可忽略): {e}")
            
            # 2. 创建新的面板
            await create_gacha_panel(self.bot, delivery_channel)
        except Exception as e:
            print(f"[面板管理] 严重错误：重建抽卡面板时失败: {e}")

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
                    # 只删除带有 "新卡速递" embed 的机器人消息
                    if message.author == self.bot.user and message.embeds:
                        if message.embeds[0].title and "新卡速递" in message.embeds[0].title:
                            try:
                                await message.delete()
                                deleted_count += 1
                            except discord.Forbidden:
                                print(f"[清理任务] 权限不足，无法删除消息 {message.id}。")
                                # 如果遇到权限问题，很可能后续也无法删除，直接停止本次任务
                                break
                            except discord.HTTPException as e:
                                print(f"[清理任务] 删除消息 {message.id} 时出错: {e}")
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

        # --- 同步逻辑 ---
        guild = interaction.guild
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        
        total_added = 0
        for forum_id in forum_ids_to_scan:
            # 确保频道属于当前服务器
            forum = guild.get_channel(forum_id)
            if not forum or not isinstance(forum, discord.ForumChannel):
                continue

            try:
                all_threads = forum.threads
                archived_threads = [t async for t in forum.archived_threads(limit=None)]
                all_threads.extend(archived_threads)
                
                thread_data = [(thread.id, forum.id, guild.id) for thread in all_threads]
                if thread_data:
                    cur.executemany("INSERT OR IGNORE INTO threads (thread_id, forum_id, guild_id) VALUES (?, ?, ?)", thread_data)
                    total_added += cur.rowcount
            except discord.Forbidden:
                print(f"[手动同步] 权限警告：无法同步论坛 {forum.mention} 的归档帖子。")
            except Exception as e:
                print(f"[手动同步] 同步论坛 '{forum.name}' 时出错: {e}")

        con.commit()
        con.close()

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
            # 获取 .env 文件的路径
            dotenv_path = os.path.join(os.getcwd(), '.env')
            # 使用 set_key 来更新 .env 文件
            set_key(dotenv_path, "DELIVERY_CHANNEL_ID", str(channel.id))
            
            # 更新 bot 实例中的在内存中的值，以便立即生效（如果可能）
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
            # 使用 unset_key 来移除 .env 文件中的键
            unset_key(dotenv_path, "DELIVERY_CHANNEL_ID")

            # 更新 bot 实例中的在内存中的值
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
            dotenv_path = os.path.join(os.getcwd(), '.env')
            # 读取现有配置
            current_ids_str = os.getenv("ALLOWED_CHANNEL_IDS", "")
            current_ids = {cid.strip() for cid in current_ids_str.split(',') if cid.strip()}
            
            # 添加新ID
            current_ids.add(str(channel.id))
            
            # 写回 .env
            new_ids_str = ",".join(current_ids)
            set_key(dotenv_path, "ALLOWED_CHANNEL_IDS", new_ids_str)

            # 更新内存中的配置
            self.bot.allowed_forum_ids = {int(cid) for cid in current_ids}

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
            dotenv_path = os.path.join(os.getcwd(), '.env')
            # 读取现有配置
            current_ids_str = os.getenv("ALLOWED_CHANNEL_IDS", "")
            current_ids = {cid.strip() for cid in current_ids_str.split(',') if cid.strip()}

            # 移除ID
            current_ids.discard(str(channel.id))

            # 写回 .env
            new_ids_str = ",".join(current_ids)
            set_key(dotenv_path, "ALLOWED_CHANNEL_IDS", new_ids_str)

            # 更新内存中的配置
            self.bot.allowed_forum_ids = {int(cid) for cid in current_ids}

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
