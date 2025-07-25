# cogs/forum_tools.py
import discord
from discord.ext import commands
from discord import app_commands
from discord.ext import tasks
import json
import os
import sqlite3
from typing import Optional

# --- 配置文件路径 ---
CONFIG_FILE = 'config.json'
DB_FILE = 'posts.db'

# --- 辅助函数：用于读写 JSON 配置文件 ---
def load_config():
    """加载配置文件，如果文件不存在或为空则创建一个新的。"""
    if not os.path.exists(CONFIG_FILE) or os.path.getsize(CONFIG_FILE) == 0:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f)
        return {}
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(data):
    """将配置数据保存到文件。"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

# --- Cog 类 ---
class ForumTools(commands.Cog):
    """
    处理与论坛频道相关的功能，包括新帖速递、配置和后台同步。
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = load_config()
        self.incremental_sync_task.start()

    def cog_unload(self):
        self.incremental_sync_task.cancel()

    @tasks.loop(hours=2)
    async def incremental_sync_task(self):
        """后台增量同步任务，只获取上次同步后产生的新帖子。"""
        await self.bot.wait_until_ready()
        print("[后台任务] 开始执行增量同步...")
        
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        
        self.config = load_config()
        total_added = 0
        
        for guild_id_str, guild_config in self.config.items():
            guild = self.bot.get_guild(int(guild_id_str))
            if not guild: continue

            forum_ids = guild_config.get("forum_channels", [])
            for forum_id in forum_ids:
                forum = guild.get_channel(forum_id)
                if not forum or not isinstance(forum, discord.ForumChannel):
                    continue
                
                try:
                    cur.execute("SELECT MAX(thread_id) FROM threads WHERE forum_id = ?", (forum_id,))
                    last_id = cur.fetchone()[0]
                    
                    if not last_id:
                        print(f"[后台任务] 论坛 '{forum.name}' 在数据库中为空，跳过。等待手动全量同步。")
                        continue

                    # 修复：ForumChannel 没有 history 方法，我们改为获取所有帖子并与数据库对比
                    # 注意：这种方法在帖子非常多时效率较低，但能保证准确性
                    all_threads_in_forum = forum.threads
                    archived_threads = [t async for t in forum.archived_threads(limit=None)]
                    all_threads_in_forum.extend(archived_threads)

                    cur.execute("SELECT thread_id FROM threads WHERE forum_id = ?", (forum_id,))
                    existing_thread_ids = {row[0] for row in cur.fetchall()}
                    
                    new_threads = [t for t in all_threads_in_forum if t.id not in existing_thread_ids]
                    
                    if new_threads:
                        thread_data = [(thread.id, forum.id, guild.id) for thread in new_threads]
                        cur.executemany("INSERT OR IGNORE INTO threads (thread_id, forum_id, guild_id) VALUES (?, ?, ?)", thread_data)
                        total_added += cur.rowcount

                except discord.Forbidden:
                    print(f"[后台任务] 权限不足，无法增量同步论坛 '{forum.name}'。")
                except Exception as e:
                    print(f"[后台任务] 增量同步论坛 '{forum.name}' 时出错: {e}")
        
        con.commit()
        con.close()
        if total_added > 0:
            print(f"[后台任务] 增量同步完成。本次新增了 {total_added} 个帖子。")

    # --- 事件监听器：当新帖子创建时 ---
    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        """
        当在任何被监控的论坛频道中创建新帖子时触发。
        同时处理新帖速递和数据库更新。
        """
        guild_id_str = str(thread.guild.id)
        forum_id = thread.parent_id

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
        # 重新加载配置以防万一
        self.config = load_config()
        guild_config = self.config.get(guild_id_str)
        if not guild_config or forum_id not in guild_config.get("forum_channels", []):
            return

        delivery_channel_id = guild_config.get("delivery_channel")
        if not delivery_channel_id:
            return
        
        delivery_channel = self.bot.get_channel(delivery_channel_id)
        if not delivery_channel:
            print(f"错误：在服务器 {thread.guild.name} 中找不到速递频道 ID: {delivery_channel_id}")
            return

        try:
            starter_message = thread.starter_message or await thread.fetch_message(thread.id)
            
            author_mention = f"**👤 作者:** {thread.owner.name}" if thread.owner else f"**👤 作者:** 未知"
            header_line = f"**{thread.name}** | {author_mention}"
            
            post_content = starter_message.content
            if len(post_content) > 400:
                post_content = post_content[:400] + "..."
            content_section = f"**📝 内容速览:**\n{post_content}"
            full_description = f"{header_line}\n\n{content_section}"

            embed = discord.Embed(
                title="✨ 新卡速递",
                description=full_description,
                color=discord.Color.blue()
            )
            embed.add_field(name="🚪 传送门", value=f"[点击查看原帖]({thread.jump_url})", inline=False)

            if starter_message.attachments:
                for attachment in starter_message.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        embed.set_thumbnail(url=attachment.url)
                        break

            if thread.applied_tags:
                tags_str = ", ".join(tag.name for tag in thread.applied_tags)
                embed.add_field(name="🏷️ 标签", value=tags_str, inline=False)

            await delivery_channel.send(embed=embed)

        except discord.errors.Forbidden:
            print(f"错误：机器人没有权限在频道 {delivery_channel.name} 中发送消息。")
        except Exception as e:
            print(f"处理新帖速递时发生未知错误: {e}")

    # --- 斜杠命令组：/config ---
    config_group = app_commands.Group(name="设置", description="配置论坛监控与速递功能", guild_only=True)

    @config_group.command(name="设置速递频道", description="设置一个频道，用于接收新帖速递通知。")
    @app_commands.describe(channel="选择一个文本频道作为速递频道")
    async def set_delivery_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """设置速递频道。"""
        # --- 从 .env 加载配置 ---
        admin_role_ids_str = os.getenv("ADMIN_ROLE_IDS", "")
        if not admin_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `ADMIN_ROLE_IDS`。", ephemeral=True)
            return

        admin_role_ids = {int(rid.strip()) for rid in admin_role_ids_str.split(',')}
        
        # --- 权限检查 ---
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(admin_role_ids):
            await interaction.response.send_message("🚫 **权限不足**：只有拥有特定管理员身份组的用户才能执行此操作。", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        
        # 初始化服务器配置
        if guild_id not in self.config:
            self.config[guild_id] = {"forum_channels": []}
            
        self.config[guild_id]["delivery_channel"] = channel.id
        save_config(self.config)
        
        await interaction.response.send_message(f"✅ 速递频道已成功设置为 {channel.mention}。", ephemeral=True)

    @config_group.command(name="添加监控论坛", description="添加一个或多个论坛频道到监控列表。")
    @app_commands.describe(channels="输入一个或多个论坛频道 (可使用 #提及 或 频道ID，用空格分隔)")
    async def add_forum_channels(self, interaction: discord.Interaction, channels: str):
        """添加一个或多个要监控的论坛频道。"""
        # --- 从 .env 加载配置 ---
        admin_role_ids_str = os.getenv("ADMIN_ROLE_IDS", "")
        if not admin_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `ADMIN_ROLE_IDS`。", ephemeral=True)
            return

        admin_role_ids = {int(rid.strip()) for rid in admin_role_ids_str.split(',')}
        
        # --- 权限检查 ---
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(admin_role_ids):
            await interaction.response.send_message("🚫 **权限不足**：只有拥有特定管理员身份组的用户才能执行此操作。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        
        if guild_id not in self.config:
            self.config[guild_id] = {"forum_channels": []}

        added_channels = []
        skipped_channels = []
        invalid_inputs = []

        # 正则表达式匹配频道提及 <#ID> 或纯数字 ID
        import re
        channel_ids = re.findall(r'<#(\d+)>|(\d+)', channels)

        for match in channel_ids:
            # match 是一个元组，例如 ('123', '') 或 ('', '456')
            channel_id_str = next((item for item in match if item), None)
            if not channel_id_str: continue

            try:
                channel_id = int(channel_id_str)
                channel = self.bot.get_channel(channel_id)

                if not channel or not isinstance(channel, discord.ForumChannel):
                    invalid_inputs.append(f"`{channel_id_str}` (非论坛频道)")
                    continue

                if channel.id not in self.config[guild_id]["forum_channels"]:
                    self.config[guild_id]["forum_channels"].append(channel.id)
                    added_channels.append(channel.mention)
                else:
                    skipped_channels.append(channel.mention)
            except ValueError:
                invalid_inputs.append(f"`{channel_id_str}` (无效ID)")

        if added_channels:
            save_config(self.config)

        # 构建反馈消息
        report = []
        if added_channels:
            report.append(f"✅ **成功添加:** {', '.join(added_channels)}")
        if skipped_channels:
            report.append(f"ℹ️ **跳过 (已存在):** {', '.join(skipped_channels)}")
        if invalid_inputs:
            report.append(f"❌ **无效输入:** {', '.join(invalid_inputs)}")
        
        if not report:
            report.append("🤔 没有任何有效的频道被输入，请检查你的输入。")

        await interaction.followup.send("\n".join(report), ephemeral=True)

    @config_group.command(name="移除监控论坛", description="从监控列表中移除一个或多个论坛频道。")
    @app_commands.describe(channels="输入一个或多个要移除的论坛频道 (可使用 #提及 或 频道ID，用空格分隔)")
    async def remove_forum_channels(self, interaction: discord.Interaction, channels: str):
        """移除一个或多个监控的论坛频道。"""
        # --- 从 .env 加载配置 ---
        admin_role_ids_str = os.getenv("ADMIN_ROLE_IDS", "")
        if not admin_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `ADMIN_ROLE_IDS`。", ephemeral=True)
            return

        admin_role_ids = {int(rid.strip()) for rid in admin_role_ids_str.split(',')}
        
        # --- 权限检查 ---
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(admin_role_ids):
            await interaction.response.send_message("🚫 **权限不足**：只有拥有特定管理员身份组的用户才能执行此操作。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)

        if guild_id not in self.config or not self.config[guild_id].get("forum_channels"):
            await interaction.followup.send("❌ 当前没有任何监控中的论坛频道。", ephemeral=True)
            return

        removed_channels = []
        not_found_channels = []
        invalid_inputs = []

        import re
        channel_ids = re.findall(r'<#(\d+)>|(\d+)', channels)

        for match in channel_ids:
            channel_id_str = next((item for item in match if item), None)
            if not channel_id_str: continue

            try:
                channel_id = int(channel_id_str)
                channel = self.bot.get_channel(channel_id)
                
                if channel_id in self.config[guild_id]["forum_channels"]:
                    self.config[guild_id]["forum_channels"].remove(channel_id)
                    removed_channels.append(channel.mention if channel else f"`{channel_id}`")
                else:
                    not_found_channels.append(channel.mention if channel else f"`{channel_id}`")
            except ValueError:
                invalid_inputs.append(f"`{channel_id_str}` (无效ID)")

        if removed_channels:
            save_config(self.config)

        # 构建反馈消息
        report = []
        if removed_channels:
            report.append(f"✅ **成功移除:** {', '.join(removed_channels)}")
        if not_found_channels:
            report.append(f"ℹ️ **未找到 (不在列表中):** {', '.join(not_found_channels)}")
        if invalid_inputs:
            report.append(f"❌ **无效输入:** {', '.join(invalid_inputs)}")

        if not report:
            report.append("🤔 没有任何有效的频道被输入，请检查你的输入。")

        await interaction.followup.send("\n".join(report), ephemeral=True)

    @config_group.command(name="查看配置", description="显示当前的速递频道和监控的论坛列表。")
    async def list_channels(self, interaction: discord.Interaction):
        """列出当前配置的频道。"""
        # --- 从 .env 加载配置 ---
        admin_role_ids_str = os.getenv("ADMIN_ROLE_IDS", "")
        if not admin_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `ADMIN_ROLE_IDS`。", ephemeral=True)
            return

        admin_role_ids = {int(rid.strip()) for rid in admin_role_ids_str.split(',')}
        
        # --- 权限检查 ---
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(admin_role_ids):
            await interaction.response.send_message("🚫 **权限不足**：只有拥有特定管理员身份组的用户才能执行此操作。", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        guild_config = self.config.get(guild_id)

        if not guild_config:
            await interaction.response.send_message("ℹ️ 当前服务器没有任何配置。", ephemeral=True)
            return

        embed = discord.Embed(title=f"⚙️ {interaction.guild.name} 的论坛监控配置", color=discord.Color.orange())

        # 显示速递频道
        delivery_channel_id = guild_config.get("delivery_channel")
        if delivery_channel_id:
            channel = self.bot.get_channel(delivery_channel_id)
            embed.add_field(name="🚚 速递频道", value=channel.mention if channel else f"ID: {delivery_channel_id} (找不到)", inline=False)
        else:
            embed.add_field(name="🚚 速递频道", value="尚未设置", inline=False)

        # 显示监控的论坛频道
        forum_ids = guild_config.get("forum_channels", [])
        if forum_ids:
            forum_mentions = []
            for fid in forum_ids:
                channel = self.bot.get_channel(fid)
                forum_mentions.append(channel.mention if channel else f"ID: {fid} (找不到)")
            embed.add_field(name="📡 监控中的论坛", value="\n".join(forum_mentions), inline=False)
        else:
            embed.add_field(name="📡 监控中的论坛", value="尚未添加任何论坛", inline=False)
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config_group.command(name="手动全量同步", description="【重要】首次配置或需要时，将所有帖子同步到数据库。")
    async def full_sync_command(self, interaction: discord.Interaction):
        """手动执行一次全量同步，获取所有活跃和归档的帖子。"""
        await interaction.response.defer(ephemeral=True, thinking=True)

        # --- 从 .env 加载配置 ---
        allowed_forum_ids_str = os.getenv("ALLOWED_CHANNEL_IDS", "")
        admin_role_ids_str = os.getenv("ADMIN_ROLE_IDS", "")

        # --- 配置有效性检查 ---
        if not allowed_forum_ids_str or not admin_role_ids_str:
            await interaction.followup.send("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `ALLOWED_CHANNEL_IDS` 或 `ADMIN_ROLE_IDS`。", ephemeral=True)
            return

        allowed_forum_ids = {int(fid.strip()) for fid in allowed_forum_ids_str.split(',')}
        admin_role_ids = {int(rid.strip()) for rid in admin_role_ids_str.split(',')}

        # --- 权限检查：检查用户是否拥有指定的管理员身份组 ---
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(admin_role_ids):
            await interaction.followup.send("🚫 **权限不足**：只有拥有特定管理员身份组的用户才能执行此操作。", ephemeral=True)
            return

        # --- 同步逻辑 ---
        guild = interaction.guild
        
        # 使用 .env 中定义的论坛ID作为扫描目标
        forum_ids_to_scan = allowed_forum_ids
        
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        
        total_added = 0
        for forum_id in forum_ids_to_scan:
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
                await interaction.followup.send(f"⚠️ **权限警告**：无法同步论坛 {forum.mention} 的归档帖子，部分历史帖子可能缺失。", ephemeral=True)
            except Exception as e:
                print(f"[手动同步] 同步论坛 '{forum.name}' 时出错: {e}")

        con.commit()
        con.close()

        await interaction.followup.send(f"✅ **全量同步完成！** 本次新增了 **{total_added}** 个帖子到总卡池中。", ephemeral=True)


# --- Cog 设置函数 ---
async def setup(bot: commands.Bot):
    await bot.add_cog(ForumTools(bot))