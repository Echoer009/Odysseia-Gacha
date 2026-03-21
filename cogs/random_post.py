# cogs/random_post.py
from __future__ import annotations

import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import random
import sqlite3
import logging
import asyncio
from typing import TYPE_CHECKING, Optional, List, Set, Dict, Any, cast

if TYPE_CHECKING:
    from bot import MyBot

# --- 数据库文件路径 ---
DB_FILE = 'posts.db'

# --- 数据库初始化 ---
def init_db():
    """初始化数据库并创建表。"""
    con = sqlite3.connect(DB_FILE, timeout=10)
    cur = con.cursor()
    # 创建帖子表
    cur.execute('''
        CREATE TABLE IF NOT EXISTS threads (
            thread_id INTEGER PRIMARY KEY,
            forum_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL
        )
    ''')
    # 创建用户偏好表
    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            selected_pools TEXT NOT NULL
        )
    ''')
    con.commit()
    con.close()

# --- 格式化帖子为 Embed 的辅助函数 ---
async def format_post_embed(interaction: discord.Interaction, thread: discord.Thread, title_prefix: str = "✨ 新卡速递") -> discord.Embed:
    """将一个帖子对象格式化为类似于新帖速递的嵌入式消息。"""
    try:
        starter_message = thread.starter_message
        if not starter_message:
            max_retries = 3
            retry_delay = 2  # 秒
            for attempt in range(max_retries):
                try:
                    starter_message = await thread.fetch_message(thread.id)
                    break  # 获取成功，跳出循环
                except discord.errors.DiscordServerError as e:
                    if attempt < max_retries - 1:
                        print(f"[抽卡模块] 获取帖子 {thread.id} 时遇到服务器503错误，将在 {retry_delay} 秒后重试...")
                        await asyncio.sleep(retry_delay)
                    else:
                        print(f"[抽卡模块] 获取帖子 {thread.id} 失败，已达到最大重试次数。")
                        raise e  # 重试耗尽，将最终错误抛出，由外层except处理
        
        # 确保 starter_message 不为 None
        if starter_message is None:
            raise ValueError("无法获取帖子的起始消息")
        
        author_mention = f"**👤 作者:** {thread.owner.name}" if thread.owner else f"**👤 作者:** 未知"
        header_line = f"**{thread.name}** | {author_mention}"
        
        post_content = starter_message.content or ""
        if len(post_content) > 400:
            post_content = post_content[:400] + "..."
        content_section = f"**📝 内容速览:**\n{post_content}"
        full_description = f"{header_line}\n\n{content_section}"

        embed = discord.Embed(
            title=title_prefix,
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
        
        parent_name = thread.parent.name if thread.parent else "未知论坛"
        embed.set_footer(text=f"来自论坛: {parent_name}")
        return embed
    except Exception as e:
        log_message = (
            f"Error formatting embed for thread ID {thread.id} ('{thread.name}') "
            f"in forum '{thread.parent.name if thread.parent else 'N/A'}'. "
            f"Triggered by {interaction.user} ({interaction.user.id})."
        )
        logging.exception(log_message)
        return discord.Embed(title="错误", description=f"无法加载帖子 {thread.name} 的信息。", color=discord.Color.red())

# --- UI 组件：卡池选择视图（带分页） ---
class PoolSelectView(discord.ui.View):
    MAX_OPTIONS_PER_PAGE = 24  # Discord Select 最多 25 个选项，留一个给"默认卡池"
    
    def __init__(self, bot: MyBot, guild_id: int, page: int = 0, selected_pools: Optional[List[str]] = None):
        super().__init__(timeout=300)
        self.bot: MyBot = bot
        self.current_page = page
        self.selected_pools = set(selected_pools) if selected_pools else set()
        
        # 获取所有可用的卡池频道（跨服务器）
        self.all_channels = self._get_all_forum_channels()
        self.total_pages = max(1, (len(self.all_channels) + self.MAX_OPTIONS_PER_PAGE - 1) // self.MAX_OPTIONS_PER_PAGE)
        
        self._update_view()

    def _get_all_forum_channels(self):
        """获取所有配置的论坛频道（跨服务器）。"""
        channels = []
        for forum_id in self.bot.allowed_forum_ids:
            channel = self.bot.get_channel(forum_id)
            if channel and isinstance(channel, discord.ForumChannel):
                # 获取分类名称（父频道）
                category_name = channel.category.name if channel.category else "未分类"
                # 格式：[服务器名] 分类名 / 频道名 用于显示
                channels.append({
                    'id': channel.id,
                    'name': channel.name,
                    'guild_name': channel.guild.name,
                    'category_name': category_name,
                    'display_name': f"[{channel.guild.name}] {category_name} / {channel.name}"
                })
        # 按服务器名、分类名、频道名排序
        channels.sort(key=lambda x: (x['guild_name'], x['category_name'], x['name']))
        return channels

    def _update_view(self):
        """更新视图组件。"""
        self.clear_items()
        
        # 添加卡池选择下拉菜单
        self.add_item(self._create_pool_select())
        
        # 如果有多页，添加翻页按钮
        if self.total_pages > 1:
            # 上一页按钮
            prev_btn = discord.ui.Button(
                label="◀ 上一页",
                style=discord.ButtonStyle.secondary,
                disabled=self.current_page == 0
            )
            prev_btn.callback = self._prev_page_callback
            self.add_item(prev_btn)
            
            # 页码显示按钮（禁用，仅显示）
            page_btn = discord.ui.Button(
                label=f"第 {self.current_page + 1}/{self.total_pages} 页",
                style=discord.ButtonStyle.secondary,
                disabled=True
            )
            self.add_item(page_btn)
            
            # 下一页按钮
            next_btn = discord.ui.Button(
                label="下一页 ▶",
                style=discord.ButtonStyle.secondary,
                disabled=self.current_page >= self.total_pages - 1
            )
            next_btn.callback = self._next_page_callback
            self.add_item(next_btn)
        
        # 添加确认按钮
        confirm_btn = discord.ui.Button(
            label="✅ 确认保存",
            style=discord.ButtonStyle.success
        )
        confirm_btn.callback = self._confirm_callback
        self.add_item(confirm_btn)

    def _create_pool_select(self):
        """创建当前页的卡池选择下拉菜单。"""
        options = [discord.SelectOption(label="默认卡池 (所有服务器所有卡池)", value="all")]
        
        # 计算当前页的频道范围
        start_idx = self.current_page * self.MAX_OPTIONS_PER_PAGE
        end_idx = min(start_idx + self.MAX_OPTIONS_PER_PAGE, len(self.all_channels))
        
        page_channels = self.all_channels[start_idx:end_idx]
        
        for channel in page_channels:
            is_selected = str(channel['id']) in self.selected_pools
            options.append(discord.SelectOption(
                label=channel['display_name'][:100],  # Discord 标签长度限制
                value=str(channel['id']),
                default=is_selected
            ))
        
        # 计算当前页最大可选数
        max_values = len(options)
        
        select = discord.ui.Select(
            placeholder=f"选择卡池 (第 {self.current_page + 1} 页，可多选)...",
            min_values=1,
            max_values=max_values,
            options=options,
            custom_id=f"pool_select_page_{self.current_page}"
        )
        select.callback = self._pool_select_callback
        return select

    async def _pool_select_callback(self, interaction: discord.Interaction):
        """处理卡池选择（临时保存选择，不写入数据库）。"""
        await interaction.response.defer()
        # 使用 cast 来告诉类型检查器 values 是一个字符串列表
        selected_values: List[str] = cast(List[str], interaction.data.get('values', [])) if interaction.data else []
        
        # 更新当前页的选择状态
        # 首先移除当前页的所有频道选择
        start_idx = self.current_page * self.MAX_OPTIONS_PER_PAGE
        end_idx = min(start_idx + self.MAX_OPTIONS_PER_PAGE, len(self.all_channels))
        page_channel_ids = {str(ch['id']) for ch in self.all_channels[start_idx:end_idx]}
        self.selected_pools -= page_channel_ids
        
        # 添加新选择
        for value in selected_values:
            if value != "all":
                self.selected_pools.add(value)
        
        # 如果选择了"all"，清空其他选择
        if "all" in selected_values:
            self.selected_pools = {"all"}

    async def _prev_page_callback(self, interaction: discord.Interaction):
        """翻到上一页。"""
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
            self._update_view()
            await interaction.edit_original_response(view=self)

    async def _next_page_callback(self, interaction: discord.Interaction):
        """翻到下一页。"""
        await interaction.response.defer()
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self._update_view()
            await interaction.edit_original_response(view=self)

    async def _confirm_callback(self, interaction: discord.Interaction):
        """确认并保存选择到数据库。"""
        await interaction.response.defer()
        
        # 如果没有手动选择任何卡池，使用默认卡池
        if not self.selected_pools:
            pools_json = json.dumps(["all"])
            selected_labels = ["默认卡池 (所有服务器所有卡池)"]
        elif "all" in self.selected_pools:
            pools_json = json.dumps(["all"])
            selected_labels = ["默认卡池 (所有服务器所有卡池)"]
        else:
            pools_json = json.dumps(list(self.selected_pools))
            selected_labels = []
            for channel_id in self.selected_pools:
                channel = self.bot.get_channel(int(channel_id))
                if channel and isinstance(channel, discord.ForumChannel):
                    category_name = channel.category.name if channel.category else "未分类"
                    selected_labels.append(f"`[{channel.guild.name}] {category_name} / {channel.name}`")
        
        # 保存到数据库
        if interaction.guild is None:
            await interaction.followup.send("⚠️ 无法在私信中保存卡池设置。", ephemeral=True)
            return
        
        con = sqlite3.connect(DB_FILE, timeout=10)
        cur = con.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO user_preferences (user_id, guild_id, selected_pools) VALUES (?, ?, ?)",
            (interaction.user.id, interaction.guild.id, pools_json)
        )
        con.commit()
        con.close()
        
        # 禁用所有组件
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        
        await interaction.edit_original_response(
            content=f"您的专属卡池已保存为: **{', '.join(selected_labels)}**，**现在是我的回合,Dolo!**",
            view=self
        )


# --- UI 组件：抽卡结果视图 (用于“再来一次”) ---
class RedrawView(discord.ui.View):
    def __init__(self, random_post_view_instance, count: int):
        super().__init__(timeout=300)  # 5分钟超时
        self.main_view = random_post_view_instance

        if count == 1:
            button = discord.ui.Button(label="再抽一发", style=discord.ButtonStyle.primary, emoji="✨")
            async def callback(interaction: discord.Interaction):
                await self.main_view._draw_posts(interaction, 1)
            button.callback = callback
            self.add_item(button)
        elif count == 5:
            button = discord.ui.Button(label="再抽五发", style=discord.ButtonStyle.success, emoji="🎇")
            async def callback(interaction: discord.Interaction):
                await self.main_view._draw_posts(interaction, 5)
            button.callback = callback
            self.add_item(button)

# --- UI 组件：主抽卡面板视图 ---
class RandomPostView(discord.ui.View):
    def __init__(self, bot: MyBot):
        super().__init__(timeout=None) # 主面板永不超时
        self.bot: MyBot = bot

    async def _draw_posts(self, interaction: discord.Interaction, count: int):
        """核心抽卡逻辑（数据库版），数据库操作已异步化。"""
        await interaction.response.defer(ephemeral=True, thinking=True)

        # 自定义异常，用于在同步函数中传递错误信息
        class DrawError(Exception):
            def __init__(self, message):
                self.message = message
                super().__init__(self.message)

        def _fetch_ids_from_db():
            """在同步函数中执行所有阻塞的数据库操作。支持跨服务器抽卡。"""
            if interaction.guild is None:
                raise DrawError("⚠️ 无法在私信中进行抽卡。")
            guild_id = interaction.guild.id
            con = None
            try:
                con = sqlite3.connect(DB_FILE, timeout=10)
                cur = con.cursor()

                # 1. 获取用户偏好
                cur.execute("SELECT selected_pools FROM user_preferences WHERE user_id = ? AND guild_id = ?", (interaction.user.id, guild_id))
                user_pref_row = cur.fetchone()
                
                target_forum_ids = []
                if user_pref_row:
                    try:
                        user_pools = json.loads(user_pref_row[0])
                        if "all" not in user_pools:
                            target_forum_ids = [int(p) for p in user_pools]
                    except (json.JSONDecodeError, TypeError):
                        raise DrawError("⚠️ 你的卡池设置似乎已损坏，请使用 `设置卡池` 功能重新设置。")

                if not target_forum_ids:
                    # 默认卡池：使用所有配置的论坛频道（跨服务器）
                    all_allowed_ids = self.bot.allowed_forum_ids
                    exclusions = self.bot.default_pool_exclusions
                    target_forum_ids = [cid for cid in all_allowed_ids if cid not in exclusions]

                if not target_forum_ids:
                    raise DrawError("🤔 无法抽卡：管理员尚未配置任何监控论坛，或者您选择的卡池为空。")

                # 2. 从数据库中根据偏好抽取帖子ID（跨服务器）
                placeholders = ','.join('?' for _ in target_forum_ids)
                cur.execute(f"SELECT thread_id FROM threads WHERE forum_id IN ({placeholders})", target_forum_ids)
                all_thread_ids = [row[0] for row in cur.fetchall()]
                
                if not all_thread_ids:
                    raise DrawError("🏜️ 所选卡池中空空如也，像你的钱包一样。等待管理员同步帖子或发布新帖吧！")
                
                return all_thread_ids

            finally:
                if con:
                    con.close()

        try:
            # --- 异步执行数据库查询 ---
            all_thread_ids = await asyncio.to_thread(_fetch_ids_from_db)
            
            # --- 帖子抽取和处理 (这部分包含异步API调用，必须在主线程) ---
            draw_count = min(count, len(all_thread_ids))
            chosen_thread_ids = random.sample(all_thread_ids, k=draw_count)
            
            embeds = []
            not_found_count = 0
            
            # 注意：为了简化，此处的清理数据库操作仍为同步，但它们在循环内部，
            # 此时 defer() 已成功，所以即使有短暂阻塞也不会导致交互超时。
            con = sqlite3.connect(DB_FILE, timeout=10)
            cur = con.cursor()
            try:
                for i, thread_id in enumerate(chosen_thread_ids):
                    try:
                        thread = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                        if not isinstance(thread, discord.Thread) or thread.flags.pinned:
                            not_found_count += 1
                            if isinstance(thread, discord.Thread) and thread.flags.pinned:
                                print(f"跳过置顶帖: {thread.name} ({thread.id})")
                            continue

                        title = f"✨ ({i+1-not_found_count}/{draw_count})" if count > 1 else "✨ 你的天选之帖"
                        embed = await format_post_embed(interaction, thread, title_prefix=title)
                        
                        if embed.title == "错误":
                            not_found_count += 1
                            cur.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
                            con.commit()
                            print(f"[抽卡模块] 清理数据库: 移除了一个帖子 (ID: {thread_id})，原因: 帖子内容(起始消息)无法加载。")
                            continue
                        embeds.append(embed)

                    except (discord.NotFound, discord.Forbidden) as e:
                        not_found_count += 1
                        cur.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
                        con.commit()
                        reason = "帖子本身已被删除" if isinstance(e, discord.NotFound) else "机器人无权访问该帖子"
                        print(f"[抽卡模块] 清理数据库: 用户 {interaction.user} (ID: {interaction.user.id}) 抽中了无法访问的帖子 (ID: {thread_id})，已自动移除。原因: {reason}")
                        continue
            finally:
                con.close()

            if not embeds:
                await interaction.followup.send("👻 很抱歉，抽中的帖子似乎都已消失在时空中...", ephemeral=True)
                return

            redraw_view = RedrawView(self, count)
            await interaction.followup.send(embeds=embeds, view=redraw_view, ephemeral=True)

        except DrawError as e:
            await interaction.followup.send(e.message, ephemeral=True)
        except Exception as e:
            logging.exception("抽卡时发生意外错误")
            await interaction.followup.send("🤯 糟糕！抽卡途中似乎遇到了一个意料之外的错误，请稍后再试或联系管理员。", ephemeral=True)

    @discord.ui.button(label="抽一张", style=discord.ButtonStyle.primary, custom_id="draw_one_button", emoji="✨")
    async def draw_one_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._draw_posts(interaction, 1)

    @discord.ui.button(label="抽五张", style=discord.ButtonStyle.success, custom_id="draw_five_button", emoji="🎇")
    async def draw_five_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._draw_posts(interaction, 5)

    @discord.ui.button(label="设置卡池", style=discord.ButtonStyle.secondary, custom_id="settings_button", emoji="🔧")
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """发送一个临时的、只有用户自己能看到的视图来选择卡池。"""
        if interaction.guild is None:
            await interaction.response.send_message("⚠️ 无法在私信中设置卡池。", ephemeral=True)
            return
        view = PoolSelectView(self.bot, interaction.guild.id)
        await interaction.response.send_message("请从下面选择你的专属抽卡范围：", view=view, ephemeral=True)


# --- 辅助函数：创建抽卡面板 ---
async def create_gacha_panel(bot: MyBot, channel: discord.TextChannel):
    """创建并发送抽卡面板到指定频道。"""
    embed = discord.Embed(
        title="🎉 类脑抽抽乐 🎉",
        description="欢迎来到类脑抽卡机！准备好迎接命运的安排了吗？!\n\n"
                    "**玩法介绍:**\n"
                    "- **抽一张 ✨**: 试试手气，看看今天的天选之卡是什么！\n"
                    "- **抽五张 🎇**: 大力出奇迹！一次性抽取五张，总有一张您喜欢！\n"
                    "- **设置卡池 🔧**: 定制您的专属卡池，只抽你最感兴趣的内容！\n\n",
        color=discord.Color.gold()
    )
    await channel.send(embed=embed, view=RandomPostView(bot))


# --- Cog 类 ---
class RandomPost(commands.Cog):
    def __init__(self, bot: MyBot):
        self.bot: MyBot = bot
        # 初始化数据库
        init_db()
        # 为了让主面板持久化，在 bot 启动时添加
        self.bot.add_view(RandomPostView(self.bot))

    @app_commands.command(name="建立随机抽取面板", description="发送一个持久化的面板，用于随机抽取帖子。")
    async def random_post_panel(self, interaction: discord.Interaction):
        """发送或重建随机帖子抽取面板。"""
        # --- 从 .env 加载配置 ---
        admin_role_ids_str = os.getenv("ADMIN_ROLE_IDS", "")
        if not admin_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `ADMIN_ROLE_IDS`。", ephemeral=True)
            return

        admin_role_ids = {int(rid.strip()) for rid in admin_role_ids_str.split(',')}
        
        # --- 权限检查 ---
        # interaction.user 可能是 User（无 roles）或 Member（有 roles）
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("🚫 **权限不足**：此命令只能在服务器中使用。", ephemeral=True)
            return
        
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(admin_role_ids):
            await interaction.response.send_message("🚫 **权限不足**：只有拥有特定管理员身份组的用户才能执行此操作。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # 检查频道类型
        channel = interaction.channel
        if channel is None or not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("❌ **错误**：此命令只能在文字频道中使用。", ephemeral=True)
            return

        # 查找并删除此频道中任何现有的抽卡面板
        async for message in channel.history(limit=100):
            if message.author == self.bot.user and message.embeds:
                if message.embeds[0].title == "🎉 类脑抽抽乐 🎉":
                    try:
                        await message.delete()
                    except discord.HTTPException as e:
                        print(f"删除旧面板时出错 (可能已被删除): {e}")
        
        # 创建新的面板
        await create_gacha_panel(self.bot, channel)
        
        await interaction.followup.send("✅ 抽卡面板已成功建立在本频道。", ephemeral=True)

# --- Cog 设置函数 ---
async def setup(bot: MyBot):
    await bot.add_cog(RandomPost(bot))