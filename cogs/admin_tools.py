# cogs/admin_tools.py
import discord
from discord.ext import commands
from discord import app_commands
import openpyxl
import sqlite3
import io

# --- 数据库文件路径 ---
DB_FILE = 'posts.db'

# --- 权限检查 ---
async def is_owner_check(interaction: discord.Interaction) -> bool:
    """检查命令使用者是否为机器人所有者。"""
    return await interaction.client.is_owner(interaction.user)

# --- Cog 类 ---
class AdminTools(commands.Cog):
    """
    包含仅限机器人所有者使用的管理工具。
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="混沌区抽卡", description="【仅限所有者】从Excel文件将帖子ID导入指定服务器。")
    @app_commands.describe(
        attachment="包含帖子ID的Excel文件 (ID应在第一列)",
        target_guild_id="帖子归属的服务器ID (Guild ID)",
        target_forum_id="帖子归属的论坛频道ID (Forum ID)"
    )
    @app_commands.check(is_owner_check)
    async def import_threads(self, interaction: discord.Interaction, attachment: discord.Attachment, target_guild_id: str, target_forum_id: str):
        """
        从上传的Excel文件中读取帖子ID，并将其与指定的目标服务器和论坛关联后存入数据库。
        """
        await interaction.response.defer(ephemeral=True, thinking=True)

        # --- 检查文件类型 ---
        if not attachment.filename.endswith(('.xlsx', '.xls')):
            await interaction.followup.send("❌ **文件格式错误**：请上传一个有效的Excel文件 (`.xlsx` 或 `.xls`)。", ephemeral=True)
            return

        # --- 验证输入的ID是否为纯数字 ---
        if not target_guild_id.isdigit() or not target_forum_id.isdigit():
            await interaction.followup.send("❌ **ID格式错误**：服务器ID和论坛ID必须是纯数字。", ephemeral=True)
            return

        try:
            # --- 读取Excel文件 ---
            file_content = await attachment.read()
            workbook = openpyxl.load_workbook(io.BytesIO(file_content))
            sheet = workbook.active
            
            thread_ids = []
            # 从第一行开始，读取第一列 (A列) 的数据
            for row in sheet.iter_rows(min_row=1, values_only=True):
                if row and row[0]:
                    try:
                        # 尝试将单元格内容强制转换为整数，这能兼容文本格式的数字
                        thread_ids.append(int(row[0]))
                    except (ValueError, TypeError):
                        # 如果转换失败（例如，内容是真正的文本），则静默跳过
                        continue

            if not thread_ids:
                await interaction.followup.send("⚠️ **未找到数据**：在Excel文件的第一列中没有找到任何有效的帖子ID。", ephemeral=True)
                return

            # --- 准备数据并存入数据库 ---
            guild_id = int(target_guild_id)
            forum_id = int(target_forum_id)
            
            thread_data = [(thread_id, forum_id, guild_id) for thread_id in thread_ids]

            con = sqlite3.connect(DB_FILE)
            cur = con.cursor()
            cur.executemany("INSERT OR IGNORE INTO threads (thread_id, forum_id, guild_id) VALUES (?, ?, ?)", thread_data)
            con.commit()
            added_count = cur.rowcount
            con.close()

            await interaction.followup.send(
                f"✅ **跨服务器导入成功！**\n"
                f"- **目标服务器ID**: `{guild_id}`\n"
                f"- **目标论坛ID**: `{forum_id}`\n"
                f"- **从文件 `{attachment.filename}` 读取了 {len(thread_ids)} 个ID**\n"
                f"- **成功向数据库新增了 {added_count} 条帖子记录。**\n"
                f"*(如果新增记录数少于读取数，说明部分帖子ID已存在于数据库中)*",
                ephemeral=True
            )

        except Exception as e:
            print(f"[Import Error] {e}")
            await interaction.followup.send(f"❌ **发生未知错误**：处理文件或数据库时出错。\n`{e}`", ephemeral=True)

    @import_threads.error
    async def on_import_threads_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("🚫 **权限不足**：你没有权限使用此命令。", ephemeral=True)
        else:
            await interaction.response.send_message(f"命令执行出错: {error}", ephemeral=True)


# --- Cog 设置函数 ---
async def setup(bot: commands.Bot):
    await bot.add_cog(AdminTools(bot))