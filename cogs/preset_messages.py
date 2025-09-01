# cogs/preset_messages.py
import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
import json
import re
from thefuzz import process, fuzz
import jieba
import time

# --- 新增：全局冷却时间 ---
# 用于存储最后一次使用命令的时间
LAST_USED_TIME = 0
COOLDOWN_DURATION = 10  # 冷却时间（秒）

def is_on_cooldown() -> bool:
    """检查是否在全局冷却期内"""
    global LAST_USED_TIME
    if time.time() - LAST_USED_TIME < COOLDOWN_DURATION:
        return True
    return False

def update_cooldown():
    """更新全局冷却时间"""
    global LAST_USED_TIME
    LAST_USED_TIME = time.time()

# --- 新增：中文停用词列表 ---
# 这些词在搜索中通常意义不大，会被过滤掉
STOP_WORDS = {
    '怎么', '的', '是', '啊', '吗', '我', '你', '他', '她', '它', '请问',
    '大佬们', '大佬', '们', '啥', '意思', '一个', '那个', '这个', '了','什么'
}

DB_FILE = 'posts.db'

def init_preset_db():
    """初始化预设消息的数据库表。"""
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS preset_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            creator_id INTEGER NOT NULL,
            UNIQUE(guild_id, name)
        )
    ''')
    con.commit()
    con.close()

# 在模块加载时立即初始化数据库
init_preset_db()

class PresetReplySelect(discord.ui.Select):
    def __init__(self, presets: list[str], target_message: discord.Message):
        self.target_message = target_message
        options = [discord.SelectOption(label=name, value=name) for name in presets]
        super().__init__(placeholder="请选择一个预设消息进行回复...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        preset_name = self.values[0]
        
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT content FROM preset_messages WHERE guild_id = ? AND name = ?", (interaction.guild.id, preset_name))
        row = cur.fetchone()
        con.close()

        if not row:
            await interaction.response.edit_message(content=f"❌ **错误**：找不到名为 `{preset_name}` 的预设消息。", view=None)
            return

        content = row[0]
        
        # --- 权限检查 ---
        user_role_ids_str = os.getenv("PRESET_USER_ROLE_IDS", "")
        # 如果没有配置，则默认拒绝，并提示服主进行配置
        if not user_role_ids_str:
            await interaction.response.edit_message(content="❌ **配置错误**：机器人管理员尚未配置 `PRESET_USER_ROLE_IDS`，无法使用此功能。", view=None)
            return
            
        user_role_ids = {int(rid.strip()) for rid in user_role_ids_str.split(',')}
        user_roles = {role.id for role in interaction.user.roles}

        # 如果用户有权限
        if user_roles.intersection(user_role_ids):
            try:
                await self.target_message.reply(content)
                # 确认是否私聊发送
                await interaction.response.edit_message(content="✅ **回复已发送！**", view=None)
                await interaction.followup.send(content="是否私聊发送给对方？", view=PrivateFollowUpView(content, target_user=self.target_message.author), ephemeral=True)
            except discord.HTTPException as e:
                await interaction.response.edit_message(content=f"❌ **回复失败**：无法发送消息。\n`{e}`", view=None)
        # 如果用户没有权限
        else:
            # 对于无权限用户，将消息内容作为临时消息发送给他们自己看
            ephemeral_content = f"🚫 **权限不足，无法公开发送**\n\n**以下是仅您可见的消息内容：**\n---\n{content}"
            await interaction.response.edit_message(content=ephemeral_content, view=None)

class PresetReplyView(discord.ui.View):
    def __init__(self, presets: list[str], target_message: discord.Message, timeout=180):
        super().__init__(timeout=timeout)
        self.add_item(PresetReplySelect(presets, target_message))

class FuzzySearchReplyView(discord.ui.View):
    """
    一个视图，为模糊搜索到的预设消息提供发送按钮。
    """
    def __init__(self, matched_presets: list[str], *, target_message: discord.Message, timeout=180):
        super().__init__(timeout=timeout)
        self.target_message = target_message  # 保存目标消息
        # 为每个匹配到的预设创建一个按钮，最多25个
        for preset_name in matched_presets[:25]:
            self.add_item(self.SendPresetButton(label=preset_name))

    class SendPresetButton(discord.ui.Button):
        def __init__(self, label: str):
            # 使用 preset name 作为 label 和 custom_id 的一部分，确保唯一性
            super().__init__(style=discord.ButtonStyle.secondary, label=label, custom_id=f"send_preset_{label}")

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer() # 先确认交互，防止超时
            preset_name = self.label

            # --- 权限检查 ---
            user_role_ids_str = os.getenv("PRESET_USER_ROLE_IDS", "")
            if not user_role_ids_str:
                await interaction.followup.send("❌ **配置错误**：机器人管理员尚未配置 `PRESET_USER_ROLE_IDS`，无法使用此功能。", ephemeral=True)
                return
            
            user_role_ids = {int(rid.strip()) for rid in user_role_ids_str.split(',')}
            user_roles = {role.id for role in interaction.user.roles}

            # --- 获取预设内容 ---
            con = sqlite3.connect(DB_FILE)
            cur = con.cursor()
            cur.execute("SELECT content FROM preset_messages WHERE guild_id = ? AND name = ?", (interaction.guild.id, preset_name))
            row = cur.fetchone()
            con.close()

            if not row:
                await interaction.followup.send(f"❌ **错误**：在数据库中找不到预设 `{preset_name}`，可能已被删除。", ephemeral=True)
                return

            content = row[0]

            # --- 根据权限发送或拒绝 ---
            if user_roles.intersection(user_role_ids):
                try:
                    await self.view.target_message.reply(content)
                    # 确认是否私聊发送
                    await interaction.followup.send(content="是否私聊发送给对方？", view=PrivateFollowUpView(content, target_user=self.view.target_message.author), ephemeral=True)
                    # 成功发送后，编辑原消息，禁用所有按钮
                    for item in self.view.children:
                        item.disabled = True
                    await interaction.edit_original_response(content=f"✅ **已回复预设消息**：`{preset_name}`", view=self.view)
                except discord.HTTPException as e:
                    await interaction.followup.send(f"❌ **发送失败**：\n`{e}`", ephemeral=True)
            else:
                # 对于无权限用户，将消息内容作为临时消息发送
                for item in self.view.children:
                    item.disabled = True
                await interaction.edit_original_response(content=f"🚫 **权限不足**：`{preset_name}` 的内容已作为临时消息发送给您。", view=self.view)
                await interaction.followup.send(content, ephemeral=True)

class PrivateFollowUpView(discord.ui.View):
    def __init__(self, content: str, *, target_user: discord.Member, timeout=180):
        super().__init__(timeout=timeout)
        self.content = content
        self.target_user = target_user

    @discord.ui.button(label="私聊发送", style=discord.ButtonStyle.primary, custom_id="private_follow_up")
    async def private_follow_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.target_user.send(self.content)
        await interaction.response.edit_message(view=None, content="✅ 已私聊发送预设消息。")

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary, custom_id="cancel_follow_up")
    async def cancel_follow_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=None, content="❌ 已取消私聊发送。")

# --- 新增：用于搜索的模态框 ---
class PresetSearchModal(discord.ui.Modal, title="搜索预设消息"):
    keyword = discord.ui.TextInput(
        label="输入关键词搜索",
        placeholder="输入关键词以筛选预设消息...",
        required=False, # 允许为空，表示显示所有
        style=discord.TextStyle.short
    )

    def __init__(self, target_message: discord.Message):
        super().__init__()
        self.target_message = target_message

    async def on_submit(self, interaction: discord.Interaction):
        search_term = self.keyword.value.lower()
        
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        # 使用 LIKE 进行模糊搜索
        cur.execute("SELECT name FROM preset_messages WHERE guild_id = ? AND name LIKE ?", (interaction.guild.id, f'%{search_term}%'))
        search_results = [row[0] for row in cur.fetchall()]
        con.close()

        if not search_results:
            await interaction.response.send_message(f"找不到包含 `{self.keyword.value}` 的预设消息。", ephemeral=True)
            return

        # 将搜索结果以新的视图（包含下拉菜单）发送
        view = PresetReplyView(search_results, self.target_message)
        await interaction.response.send_message("请从搜索结果中选择：", view=view, ephemeral=True)

class PresetMessageCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 原有的右键菜单
        self.reply_context_menu = app_commands.ContextMenu(
            name='💬 使用预设消息回复',
            callback=self.reply_with_preset_context_menu,
        )
        self.bot.tree.add_command(self.reply_context_menu)

        # 新增的右键菜单：从消息中检索
        self.search_context_menu = app_commands.ContextMenu(
            name='🔍从消息中检索预设消息',
            callback=self.search_from_message_context_menu,
        )
        self.bot.tree.add_command(self.search_context_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.reply_context_menu.name, type=self.reply_context_menu.type)
        self.bot.tree.remove_command(self.search_context_menu.name, type=self.search_context_menu.type)

    preset_group = app_commands.Group(name="预设消息", description="管理和发送预设消息")

    @preset_group.command(name="添加", description="通过消息链接添加一个新的预设消息。")
    @app_commands.describe(
        name="预设的唯一名称",
        message_link="包含预设内容和图片的消息链接"
    )
    async def add_preset(self, interaction: discord.Interaction, name: str, message_link: str):
        """通过解析一个消息链接来添加或更新预设消息。"""
        # --- 权限检查 (复用逻辑) ---
        creator_role_ids_str = os.getenv("PRESET_CREATOR_ROLE_IDS", "")
        if not creator_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `PRESET_CREATOR_ROLE_IDS`。", ephemeral=True)
            return
        creator_role_ids = {int(rid.strip()) for rid in creator_role_ids_str.split(',')}
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(creator_role_ids):
            await interaction.response.send_message("🚫 **权限不足**：只有拥有特定身份组的用户才能执行此操作。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # --- 从链接获取消息 ---
        match = re.match(r'https://discord.com/channels/(\d+)/(\d+)/(\d+)', message_link)
        if not match:
            await interaction.followup.send("❌ **链接无效**：请输入一个有效的 Discord 消息链接。", ephemeral=True)
            return

        guild_id, channel_id, message_id = map(int, match.groups())

        if guild_id != interaction.guild.id:
            await interaction.followup.send("❌ **操作无效**：不能从其他服务器的消息创建预设。", ephemeral=True)
            return

        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                 await interaction.followup.send("❌ **频道类型错误**：链接必须指向一个文本频道或帖子。", ephemeral=True)
                 return
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            await interaction.followup.send("❌ **错误**：找不到链接对应的消息，请检查链接是否正确或消息是否已被删除。", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send("❌ **权限不足**：我没有权限读取该频道的消息。", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ **未知错误**：获取消息时出错。\n`{e}`", ephemeral=True)
            return

        # --- 准备内容 ---
        final_content = message.content
        if message.attachments:
            # 将所有附件的URL附加到内容后面
            urls = [att.url for att in message.attachments]
            if final_content: # 如果已有文本内容，则换行
                final_content += "\n" + "\n".join(urls)
            else: # 如果没有文本内容，直接就是url
                final_content = "\n".join(urls)


        # --- 数据库操作 ---
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        try:
            # 使用 INSERT OR REPLACE 逻辑，如果存在同名预设则更新它
            cur.execute(
                """
                INSERT INTO preset_messages (guild_id, name, content, creator_id) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, name) DO UPDATE SET
                content = excluded.content,
                creator_id = excluded.creator_id;
                """,
                (interaction.guild.id, name, final_content, interaction.user.id)
            )
            con.commit()
            await interaction.followup.send(f"✅ 预设消息 `{name}` 已成功创建/更新！", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ **数据库错误**：无法创建或更新预设消息。\n`{e}`", ephemeral=True)
        finally:
            con.close()

    @preset_group.command(name="覆盖", description="通过消息链接覆盖一个已有的预设消息。")
    @app_commands.describe(
        name="要覆盖的预设的名称",
        message_link="包含新内容的消息链接"
    )
    async def override_preset(self, interaction: discord.Interaction, name: str, message_link: str):
        """通过解析一个消息链接来覆盖一个已有的预设消息。"""
        # --- 权限检查 (复用逻辑) ---
        creator_role_ids_str = os.getenv("PRESET_CREATOR_ROLE_IDS", "")
        if not creator_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `PRESET_CREATOR_ROLE_IDS`。", ephemeral=True)
            return
        creator_role_ids = {int(rid.strip()) for rid in creator_role_ids_str.split(',')}
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(creator_role_ids):
            await interaction.response.send_message("🚫 **权限不足**：只有拥有特定身份组的用户才能执行此操作。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # --- 检查预设是否存在 ---
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT id FROM preset_messages WHERE guild_id = ? AND name = ?", (interaction.guild.id, name))
        if not cur.fetchone():
            con.close()
            await interaction.followup.send(f"❌ **错误**：找不到名为 `{name}` 的预设消息，无法覆盖。", ephemeral=True)
            return
        con.close()


        # --- 从链接获取消息 ---
        match = re.match(r'https://discord.com/channels/(\d+)/(\d+)/(\d+)', message_link)
        if not match:
            await interaction.followup.send("❌ **链接无效**：请输入一个有效的 Discord 消息链接。", ephemeral=True)
            return

        guild_id, channel_id, message_id = map(int, match.groups())

        if guild_id != interaction.guild.id:
            await interaction.followup.send("❌ **操作无效**：不能从其他服务器的消息创建预设。", ephemeral=True)
            return

        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                 await interaction.followup.send("❌ **频道类型错误**：链接必须指向一个文本频道或帖子。", ephemeral=True)
                 return
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            await interaction.followup.send("❌ **错误**：找不到链接对应的消息，请检查链接是否正确或消息是否已被删除。", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send("❌ **权限不足**：我没有权限读取该频道的消息。", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ **未知错误**：获取消息时出错。\n`{e}`", ephemeral=True)
            return

        # --- 准备内容 ---
        final_content = message.content
        if message.attachments:
            # 将所有附件的URL附加到内容后面
            urls = [att.url for att in message.attachments]
            if final_content: # 如果已有文本内容，则换行
                final_content += "\n" + "\n".join(urls)
            else: # 如果没有文本内容，直接就是url
                final_content = "\n".join(urls)


        # --- 数据库操作 ---
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        try:
            # 使用 INSERT OR REPLACE 逻辑，如果存在同名预设则更新它
            cur.execute(
                """
                INSERT INTO preset_messages (guild_id, name, content, creator_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, name) DO UPDATE SET
                content = excluded.content,
                creator_id = excluded.creator_id;
                """,
                (interaction.guild.id, name, final_content, interaction.user.id)
            )
            con.commit()
            await interaction.followup.send(f"✅ 预设消息 `{name}` 已成功被新内容覆盖！", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ **数据库错误**：无法覆盖预设消息。\n`{e}`", ephemeral=True)
        finally:
            con.close()

    @override_preset.autocomplete('name')
    async def override_preset_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT name FROM preset_messages WHERE guild_id = ? AND name LIKE ?", (interaction.guild.id, f'%{current}%'))
        all_presets = [row[0] for row in cur.fetchall()]
        con.close()

        return [
            app_commands.Choice(name=preset, value=preset)
            for preset in all_presets
        ][:25]

    @preset_group.command(name="删除", description="删除一个已有的预设消息")
    @app_commands.describe(name="要删除的预设消息的名称")
    async def remove_preset(self, interaction: discord.Interaction, name: str):
        """处理删除预设消息的命令。"""
        # --- 从 .env 加载配置 ---
        creator_role_ids_str = os.getenv("PRESET_CREATOR_ROLE_IDS", "")
        if not creator_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `PRESET_CREATOR_ROLE_IDS`。", ephemeral=True)
            return

        creator_role_ids = {int(rid.strip()) for rid in creator_role_ids_str.split(',')}
        
        # --- 权限检查 ---
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(creator_role_ids):
            await interaction.response.send_message("🚫 **权限不足**：只有拥有特定身份组的用户才能执行此操作。", ephemeral=True)
            return

        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("DELETE FROM preset_messages WHERE guild_id = ? AND name = ?", (interaction.guild.id, name))
        
        if cur.rowcount > 0:
            con.commit()
            await interaction.response.send_message(f"✅ 预设消息 `{name}` 已成功删除。", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ **错误**：找不到名为 `{name}` 的预设消息。", ephemeral=True)
        
        con.close()

    @remove_preset.autocomplete('name')
    async def remove_preset_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT name FROM preset_messages WHERE guild_id = ?", (interaction.guild.id,))
        all_presets = [row[0] for row in cur.fetchall()]
        con.close()

        return [
            app_commands.Choice(name=preset, value=preset)
            for preset in all_presets if current.lower() in preset.lower()
        ]

    @preset_group.command(name="列表", description="查看所有可用的预设消息")
    async def list_presets(self, interaction: discord.Interaction):
        """处理列出预设消息的命令。"""
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT name FROM preset_messages WHERE guild_id = ?", (interaction.guild.id,))
        all_presets = [row[0] for row in cur.fetchall()]
        con.close()

        if not all_presets:
            await interaction.response.send_message("ℹ️ 当前服务器还没有任何预设消息。", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📜 {interaction.guild.name} 的预设消息列表",
            description="以下是所有可用的预设消息名称：",
            color=discord.Color.green()
        )
        
        # 将预设消息列表格式化为更美观的格式
        formatted_list = "\n".join(f"- `{name}`" for name in all_presets)
        embed.add_field(name="可用名称", value=formatted_list, inline=False)
        
        embed.set_footer(text=f"共 {len(all_presets)} 条预设消息 | 右键消息 -> 应用 -> 使用预设消息回复")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @preset_group.command(name="导入json", description="上传一个JSON文件来批量导入预设消息 (仅限管理员)")
    @app_commands.describe(attachment="包含预设消息的JSON文件")
    async def import_presets(self, interaction: discord.Interaction, attachment: discord.Attachment):
        """通过上传的JSON文件批量导入预设消息。"""
        # --- 权限检查 (复用 PRESET_CREATOR_ROLE_IDS) ---
        creator_role_ids_str = os.getenv("PRESET_CREATOR_ROLE_IDS", "")
        if not creator_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `PRESET_CREATOR_ROLE_IDS`。", ephemeral=True)
            return
        creator_role_ids = {int(rid.strip()) for rid in creator_role_ids_str.split(',')}
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles.intersection(creator_role_ids):
            await interaction.response.send_message("🚫 **权限不足**：只有拥有特定身份组的用户才能执行此操作。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            # 检查文件类型
            if not attachment.filename.lower().endswith('.json'):
                await interaction.followup.send("❌ **文件类型错误**：请上传一个 `.json` 文件。", ephemeral=True)
                return

            # 读取附件内容
            file_content = await attachment.read()
            data_to_import = json.loads(file_content.decode('utf-8'))

            if not isinstance(data_to_import, list):
                await interaction.followup.send("❌ **格式错误**：JSON 文件的顶层结构必须是一个数组 `[...]`。", ephemeral=True)
                return

            con = sqlite3.connect(DB_FILE)
            cur = con.cursor()
            
            added_count = 0
            skipped_count = 0
            error_list = []

            for item in data_to_import:
                if not isinstance(item, dict) or 'name' not in item or 'value' not in item:
                    error_list.append(f"无效条目: `{item}` (缺少 name 或 value)")
                    continue
                
                preset_name = item['name']
                preset_content = item['value']

                try:
                    cur.execute(
                        "INSERT INTO preset_messages (guild_id, name, content, creator_id) VALUES (?, ?, ?, ?)",
                        (interaction.guild.id, preset_name, preset_content, interaction.user.id)
                    )
                    added_count += 1
                except sqlite3.IntegrityError:
                    skipped_count += 1
            
            con.commit()
            con.close()

            report = [f"✅ **导入成功:** {added_count} 条"]
            if skipped_count > 0:
                report.append(f"ℹ️ **跳过 (名称已存在):** {skipped_count} 条")
            if error_list:
                report.append(f"❌ **格式错误:**\n" + "\n".join(error_list))
                
            await interaction.followup.send("\n".join(report), ephemeral=True)

        except Exception as e:
            # 捕获所有其他潜在错误，防止命令卡住
            print(f"[导入错误] {type(e).__name__}: {e}")
            await interaction.followup.send(f"❌ **发生未知错误**：导入过程中断。\n请检查控制台日志以获取详细信息。\n`{e}`", ephemeral=True)


    async def reply_with_preset_context_menu(self, interaction: discord.Interaction, message: discord.Message):
        """右键菜单命令的回调函数，现在弹出搜索模态框。"""
        # 检查是否在全局冷却期内
        if is_on_cooldown():
            remaining_time = int(COOLDOWN_DURATION - (time.time() - LAST_USED_TIME))
            await interaction.response.send_message(f"⏳ **命令冷却中**：请等待 {remaining_time} 秒后再试。", ephemeral=True)
            return
        
        # 更新全局冷却时间
        update_cooldown()
        
        # 检查服务器是否有任何预设消息
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT 1 FROM preset_messages WHERE guild_id = ? LIMIT 1", (interaction.guild.id,))
        has_presets = cur.fetchone()
        con.close()

        if not has_presets:
            await interaction.response.send_message("ℹ️ 当前服务器还没有任何预设消息，无法进行回复。", ephemeral=True)
            return
            
        # 弹出搜索模态框，并将目标消息传递过去
        modal = PresetSearchModal(target_message=message)
        await interaction.response.send_modal(modal)

    # --- 修改后的斜杠命令：通过@用户发送 ---
    @preset_group.command(name="发送给", description="通过@用户并发送预设消息。")
    @app_commands.describe(
        user="要@的用户",
        name="要使用的预设消息的名称",
        send_to_user="是否私聊发送给用户"
    )
    async def reply_with_preset_slash(self, interaction: discord.Interaction, user: discord.Member, name: str, send_to_user: bool = False):
        """通过@用户并发送预设消息，模拟回复效果。"""
        # 检查是否在全局冷却期内
        if is_on_cooldown():
            remaining_time = int(COOLDOWN_DURATION - (time.time() - LAST_USED_TIME))
            await interaction.response.send_message(f"⏳ **命令冷却中**：请等待 {remaining_time} 秒后再试。", ephemeral=True)
            return
        
        # 更新全局冷却时间
        update_cooldown()
        
        # 1. 从数据库获取预设内容
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT content FROM preset_messages WHERE guild_id = ? AND name = ?", (interaction.guild.id, name))
        row = cur.fetchone()
        con.close()

        if not row:
            await interaction.response.send_message(f"❌ **错误**：找不到名为 `{name}` 的预设消息。请检查您的输入。", ephemeral=True)
            return
        
        content = row[0]

        # --- 权限检查 ---
        user_role_ids_str = os.getenv("PRESET_USER_ROLE_IDS", "")
        if not user_role_ids_str:
            await interaction.response.send_message("❌ **配置错误**：机器人管理员尚未在 `.env` 文件中配置 `PRESET_USER_ROLE_IDS`，无法使用此功能。", ephemeral=True)
            return

        user_role_ids = {int(rid.strip()) for rid in user_role_ids_str.split(',')}
        user_roles = {role.id for role in interaction.user.roles}

        # 如果用户有权限
        if user_roles.intersection(user_role_ids):
            message_to_send = f"{user.mention}\n{content}"
            try:
                await interaction.channel.send(message_to_send)
                # 私聊同步发送
                if send_to_user:
                    await user.send(message_to_send)
                await interaction.response.send_message(f"✅ 已向 {user.display_name} 发送预设消息 `{name}`。", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.response.send_message(f"❌ **发送失败**：无法发送消息。\n`{e}`", ephemeral=True)
        # 如果用户没有权限
        else:
            # 对于无权限用户，将消息内容作为临时消息发送给他们自己看
            ephemeral_content = f"🚫 **权限不足，无法公开发送给 {user.mention}**\n\n**以下是仅您可见的消息内容：**\n---\n{content}"
            await interaction.response.send_message(ephemeral_content, ephemeral=True)

    @reply_with_preset_slash.autocomplete('name')
    async def reply_with_preset_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """为 /preset_reply 命令的 name 参数提供自动补全。"""
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT name FROM preset_messages WHERE guild_id = ? AND name LIKE ?", (interaction.guild.id, f'%{current}%'))
        all_presets = [row[0] for row in cur.fetchall()]
        con.close()

        return [
            app_commands.Choice(name=preset, value=preset)
            for preset in all_presets
        ][:25] # Autocomplete最多只能显示25个选项

    async def search_from_message_context_menu(self, interaction: discord.Interaction, message: discord.Message):
        """新的右键菜单命令，用于从消息内容中检索并发送预设。"""
        # 检查是否在全局冷却期内
        if is_on_cooldown():
            remaining_time = int(COOLDOWN_DURATION - (time.time() - LAST_USED_TIME))
            await interaction.response.send_message(f"⏳ **命令冷却中**：请等待 {remaining_time} 秒后再试。", ephemeral=True)
            return
        
        # 更新全局冷却时间
        update_cooldown()
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        if not message.content:
            await interaction.followup.send("❌ 目标消息没有文本内容可供检索。", ephemeral=True)
            return

        raw_query = message.content
        
        # 从数据库获取所有预设
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT name, content FROM preset_messages WHERE guild_id = ?", (interaction.guild.id,))
        all_presets = cur.fetchall() # [(name, content), ...]
        con.close()

        if not all_presets:
            await interaction.followup.send("ℹ️ 当前服务器还没有任何预设消息。", ephemeral=True)
            return

        # --- 最终版 Pro Max：动态相关性过滤策略 ---
        
        # 1. 分词并过滤停用词
        raw_keywords = jieba.cut_for_search(raw_query)
        query_keywords = {k.lower() for k in raw_keywords if k not in STOP_WORDS and k.strip()}
        if not query_keywords:
            query_keywords = {k.lower() for k in raw_keywords if k.strip()}
        # 2. 超级加权计分
        scores = {}
        for name, content in all_presets:
            current_score = 0
            for keyword in query_keywords:
                if keyword in name.lower():
                    current_score += 10  # 名称中匹配，权重极高
                if keyword in content.lower():
                    current_score += 1   # 内容中匹配，权重较低
            if current_score > 0:
                scores[name] = current_score

        # 3. 动态阈值过滤
        if not scores:
            final_matches = []
        else:
            max_score = max(scores.values())
            # 及格线设为最高分的40%，但最低不能低于2分
            score_threshold = max(max_score * 0.4, 2)
            
            # 筛选出所有高于及格线的
            passed_matches = {name: score for name, score in scores.items() if score >= score_threshold}
            
            # 按分数排序
            sorted_matches = sorted(passed_matches.items(), key=lambda item: item[1], reverse=True)
            final_matches = [name for name, score in sorted_matches]

        if not final_matches:
            await interaction.followup.send(f"ℹ️ 未能从预设消息的 **名称** 或 **内容** 中找到与 `{message.content}` 高度相关的结果。", ephemeral=True)
            return
        
        # 创建并发送带有按钮的视图
        view = FuzzySearchReplyView(final_matches[:25], target_message=message) # 最多显示25个按钮
        await interaction.followup.send("🔍 **检索到以下高度相关的预设消息：**\n请点击按钮直接回复。", view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PresetMessageCog(bot))