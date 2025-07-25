# cogs/random_post.py
import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import random
import sqlite3

# --- 配置文件路径 ---
CONFIG_FILE = 'config.json'
DB_FILE = 'posts.db'

# --- 辅助函数：加载配置 ---
def load_config():
    """加载配置文件。"""
    if not os.path.exists(CONFIG_FILE) or os.path.getsize(CONFIG_FILE) == 0:
        return {}
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

# --- 数据库初始化 ---
def init_db():
    """初始化数据库并创建表。"""
    con = sqlite3.connect(DB_FILE)
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
async def format_post_embed(thread: discord.Thread, title_prefix: str = "✨ 新卡速递") -> discord.Embed:
    """将一个帖子对象格式化为类似于新帖速递的嵌入式消息。"""
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
            
        embed.set_footer(text=f"来自论坛: {thread.parent.name}")
        return embed
    except Exception as e:
        print(f"格式化帖子 Embed 时出错: {e}")
        return discord.Embed(title="错误", description=f"无法加载帖子 {thread.name} 的信息。", color=discord.Color.red())

# --- UI 组件：卡池选择视图 ---
class PoolSelectView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(self.create_pool_select(guild_id))

    def create_pool_select(self, guild_id: int):
        """动态创建支持多选的卡池选择下拉菜单。"""
        options = [discord.SelectOption(label="默认卡池 (所有卡池)", value="all")]
        config = load_config()
        guild_config = config.get(str(guild_id))
        
        valid_options_count = 0
        if guild_config:
            forum_ids = guild_config.get("forum_channels", [])
            for forum_id in forum_ids:
                channel = self.bot.get_channel(forum_id)
                if channel and isinstance(channel, discord.ForumChannel):
                    options.append(discord.SelectOption(label=f"卡池: {channel.name}", value=str(channel.id)))
                    valid_options_count += 1
        
        select = discord.ui.Select(
            placeholder="选择你的专属卡池 (可多选)...",
            min_values=1,
            max_values=max(1, valid_options_count + 1),
            options=options,
            custom_id="pool_select_db"
        )
        select.callback = self.pool_select_callback
        return select

    async def pool_select_callback(self, interaction: discord.Interaction):
        """处理卡池选择，并将结果存入数据库。"""
        selected_values = interaction.data['values']
        
        # 将选择的列表转换为 JSON 字符串
        pools_json = json.dumps(selected_values)
        
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO user_preferences (user_id, guild_id, selected_pools) VALUES (?, ?, ?)",
            (interaction.user.id, interaction.guild.id, pools_json)
        )
        con.commit()
        con.close()

        # 生成反馈信息
        if "all" in selected_values:
            selected_labels = ["默认卡池 (所有卡池)"]
        else:
            selected_labels = []
            for value in selected_values:
                channel = self.bot.get_channel(int(value))
                if channel:
                    selected_labels.append(f"`{channel.name}`")

        # 禁用所有组件
        for item in self.children:
            item.disabled = True
        # 编辑原始消息，显示确认信息并更新视图
        await interaction.response.edit_message(content=f"您的专属卡池已保存为: **{', '.join(selected_labels)}**,**现在是我的回合,Dolo!**", view=self)


# --- UI 组件：主抽卡面板视图 ---
class RandomPostView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None) # 主面板永不超时
        self.bot = bot

    async def _draw_posts(self, interaction: discord.Interaction, count: int):
        """核心抽卡逻辑（数据库版）。"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = interaction.guild.id
        con = None  # 初始化 con
        try:
            con = sqlite3.connect(DB_FILE)
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
                    await interaction.followup.send("⚠️ 你的卡池设置似乎已损坏，请使用 `设置卡池` 功能重新设置。", ephemeral=True)
                    return # 直接返回，中断抽卡

            # 如果没有偏好或偏好是 "all"，则获取服务器所有监控的论坛
            if not target_forum_ids:
                config = load_config()
                guild_config = config.get(str(guild_id))
                if guild_config:
                    target_forum_ids = guild_config.get("forum_channels", [])

            if not target_forum_ids:
                await interaction.followup.send("🤔 无法抽卡：管理员尚未配置任何监控论坛，或者您选择的卡池为空。", ephemeral=True)
                return

            # 2. 从数据库中根据偏好抽取帖子ID
            placeholders = ','.join('?' for _ in target_forum_ids)
            cur.execute(f"SELECT thread_id FROM threads WHERE guild_id = ? AND forum_id IN ({placeholders})", [guild_id] + target_forum_ids)
            all_thread_ids = [row[0] for row in cur.fetchall()]
            
            if not all_thread_ids:
                await interaction.followup.send("🏜️ 所选卡池中空空如也，像你的钱包一样。等待管理员同步帖子或发布新帖吧！", ephemeral=True)
                return
            
            # 3. 抽取并获取帖子信息
            draw_count = min(count, len(all_thread_ids))
            chosen_thread_ids = random.sample(all_thread_ids, k=draw_count)
            
            embeds = []
            not_found_count = 0
            for i, thread_id in enumerate(chosen_thread_ids):
                try:
                    thread = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                    if not isinstance(thread, discord.Thread):
                        not_found_count += 1
                        continue
                    # 移除 "抽卡结果" 字样，直接显示帖子标题
                    title = f"✨ ({i+1-not_found_count}/{draw_count})" if count > 1 else "✨ 你的天选之帖"
                    embeds.append(await format_post_embed(thread, title_prefix=title))
                except (discord.NotFound, discord.Forbidden):
                    not_found_count += 1
                    continue
            
            if not embeds:
                await interaction.followup.send("👻 很抱歉，抽中的帖子似乎都已消失在时空中...", ephemeral=True)
                return

            await interaction.followup.send(embeds=embeds, ephemeral=True)

        except Exception as e:
            print(f"抽卡时发生意外错误: {e}")
            await interaction.followup.send("🤯 糟糕！抽卡途中似乎遇到了一个意料之外的错误，请稍后再试或联系管理员。", ephemeral=True)
        finally:
            if con:
                con.close()

    @discord.ui.button(label="抽一张", style=discord.ButtonStyle.primary, custom_id="draw_one_button", emoji="✨")
    async def draw_one_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._draw_posts(interaction, 1)

    @discord.ui.button(label="抽五张", style=discord.ButtonStyle.success, custom_id="draw_five_button", emoji="🎇")
    async def draw_five_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._draw_posts(interaction, 5)

    @discord.ui.button(label="设置卡池", style=discord.ButtonStyle.secondary, custom_id="settings_button", emoji="🔧")
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """发送一个临时的、只有用户自己能看到的视图来选择卡池。"""
        view = PoolSelectView(self.bot, interaction.guild.id)
        await interaction.response.send_message("请从下面选择你的专属抽卡范围：", view=view, ephemeral=True)


# --- Cog 类 ---
class RandomPost(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 初始化数据库
        init_db()
        # 为了让主面板持久化，在 bot 启动时添加
        self.bot.add_view(RandomPostView(self.bot))

    @app_commands.command(name="建立随机抽取面板", description="发送一个持久化的面板，用于随机抽取帖子。")
    async def random_post_panel(self, interaction: discord.Interaction):
        """发送随机帖子抽取面板。"""
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

        embed = discord.Embed(
            title="🎉 类脑抽抽乐 🎉",
            description="欢迎来到类脑抽卡机！准备好迎接命运的安排了吗？!\n\n"
                        "**玩法介绍:**\n"
                        "- **抽一张 ✨**: 试试手气，看看今天的天选之卡是什么！\n"
                        "- **抽五张 🎇**: 大力出奇迹！一次性抽取五张，总有一张您喜欢！\n"
                        "- **设置卡池 🔧**: 定制您的专属卡池，只抽你最感兴趣的内容！\n\n",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, view=RandomPostView(self.bot))

# --- Cog 设置函数 ---
async def setup(bot: commands.Bot):
    await bot.add_cog(RandomPost(bot))