# cogs/preset_messages.py
import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os

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
class PresetMessageModal(discord.ui.Modal, title="创建新的预设消息"):
    name = discord.ui.TextInput(
        label="预设名称 (用于调用)",
        placeholder="例如：欢迎语",
        required=True,
        style=discord.TextStyle.short
    )
    content = discord.ui.TextInput(
        label="预设内容",
        placeholder="输入你想要设置为预设的完整消息内容...",
        required=True,
        style=discord.TextStyle.long
    )

    async def on_submit(self, interaction: discord.Interaction):
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        try:
            cur.execute(
                "INSERT INTO preset_messages (guild_id, name, content, creator_id) VALUES (?, ?, ?, ?)",
                (interaction.guild.id, self.name.value, self.content.value, interaction.user.id)
            )
            con.commit()
            await interaction.response.send_message(f"✅ 预设消息 `{self.name.value}` 已成功创建！", ephemeral=True)
        except sqlite3.IntegrityError:
            await interaction.response.send_message(f"❌ **错误**：名为 `{self.name.value}` 的预设消息已存在。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ **数据库错误**：无法创建预设消息。\n`{e}`", ephemeral=True)
        finally:
            con.close()
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

    @preset_group.command(name="添加", description="添加一个新的预设消息")
    async def add_preset(self, interaction: discord.Interaction):
        """处理添加预设消息的命令。"""
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

        # --- 弹出表单 ---
        await interaction.response.send_modal(PresetMessageModal())

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


    async def reply_with_preset_context_menu(self, interaction: discord.Interaction, message: discord.Message):
        """右键菜单命令的回调函数。"""
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT name FROM preset_messages WHERE guild_id = ?", (interaction.guild.id,))
        all_presets = [row[0] for row in cur.fetchall()]
        con.close()

        if not all_presets:
            await interaction.response.send_message("ℹ️ 当前服务器还没有任何预设消息，无法进行回复。", ephemeral=True)
            return

        view = PresetReplyView(all_presets, message)
        await interaction.response.send_message("请选择要用于回复的预设消息：", view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PresetMessageCog(bot))