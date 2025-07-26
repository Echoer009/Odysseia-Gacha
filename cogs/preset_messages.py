# cogs/preset_messages.py
import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
import json

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

        if row:
            content = row[0]
            try:
                # 尝试回复目标消息
                await self.target_message.reply(content)
                # 用户要求移除成功提示，所以我们只在交互成功后静默处理
                # 使用 edit a new message with no content to dismiss the "thinking" state
                await interaction.response.edit_message(content="✅", view=None)
            except discord.HTTPException as e:
                await interaction.response.edit_message(content=f"❌ **回复失败**：无法发送消息。\n`{e}`", view=None)
        else:
            await interaction.response.send_message(f"❌ **错误**：找不到名为 `{preset_name}` 的预设消息。", ephemeral=True)

class PresetReplyView(discord.ui.View):
    def __init__(self, presets: list[str], target_message: discord.Message, timeout=180):
        super().__init__(timeout=timeout)
        self.add_item(PresetReplySelect(presets, target_message))

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
        self.reply_context_menu = app_commands.ContextMenu(
            name='💬 使用预设消息回复',
            callback=self.reply_with_preset_context_menu,
        )
        self.bot.tree.add_command(self.reply_context_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.reply_context_menu.name, type=self.reply_context_menu.type)

    preset_group = app_commands.Group(name="预设消息", description="管理和发送预设消息")

    @preset_group.command(name="添加", description="添加一个新的预设消息，可附带多张图片。")
    @app_commands.describe(
        name="预设的唯一名称",
        content="预设的文本内容",
        image1="（可选）要附加的第1张图片",
        image2="（可选）要附加的第2张图片",
        image3="（可选）要附加的第3张图片",
        image4="（可选）要附加的第4张图片",
        image5="（可选）要附加的第5张图片"
    )
    async def add_preset(self, interaction: discord.Interaction, name: str, content: str,
                         image1: discord.Attachment = None,
                         image2: discord.Attachment = None,
                         image3: discord.Attachment = None,
                         image4: discord.Attachment = None,
                         image5: discord.Attachment = None):
        """处理添加预设消息的命令，支持文本和最多5张可选图片。"""
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

        # --- 准备要存入数据库的内容 ---
        final_content = content
        images = [img for img in [image1, image2, image3, image4, image5] if img is not None]

        if images:
            # 1. 验证所有附件是否都是图片
            for image in images:
                if not image.content_type or not image.content_type.startswith('image/'):
                    await interaction.response.send_message(f"❌ **文件类型错误**：文件 `{image.filename}` 不是一个有效的图片文件。", ephemeral=True)
                    return
            
            # 2. 如果全部有效，则附加所有URL
            for image in images:
                final_content += f"\n{image.url}"

        # --- 数据库操作 ---
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        try:
            cur.execute(
                "INSERT INTO preset_messages (guild_id, name, content, creator_id) VALUES (?, ?, ?, ?)",
                (interaction.guild.id, name, final_content, interaction.user.id)
            )
            con.commit()
            await interaction.response.send_message(f"✅ 预设消息 `{name}` 已成功创建！", ephemeral=True)
        except sqlite3.IntegrityError:
            await interaction.response.send_message(f"❌ **错误**：名为 `{name}` 的预设消息已存在。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ **数据库错误**：无法创建预设消息。\n`{e}`", ephemeral=True)
        finally:
            con.close()

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
        name="要使用的预设消息的名称"
    )
    async def reply_with_preset_slash(self, interaction: discord.Interaction, user: discord.Member, name: str):
        """通过@用户并发送预设消息，模拟回复效果。"""
        # 1. 从数据库获取预设内容
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT content FROM preset_messages WHERE guild_id = ? AND name = ?", (interaction.guild.id, name))
        row = cur.fetchone()
        con.close()

        if not row:
            await interaction.response.send_message(f"❌ **错误**：找不到名为 `{name}` 的预设消息。请检查您的输入。", ephemeral=True)
            return
        
        # 2. 构造并发送消息
        content = row[0]
        # 构造提及用户的消息
        message_to_send = f"{user.mention}\n{content}"

        try:
            # 在当前频道发送消息，因为没有原始消息可以回复
            await interaction.channel.send(message_to_send)
            # 确认交互成功
            await interaction.response.send_message(f"✅ 已向 {user.display_name} 发送预设消息 `{name}`。", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ **发送失败**：无法发送消息。\n`{e}`", ephemeral=True)

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


async def setup(bot: commands.Bot):
    await bot.add_cog(PresetMessageCog(bot))