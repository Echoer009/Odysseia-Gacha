# cogs/context_menu.py
import discord
from discord.ext import commands
from discord import app_commands

# --- 消息上下文菜单命令 ---
# 根据 discord.py 的要求，上下文菜单命令必须在顶层定义，不能在 Cog 类内部。
@app_commands.context_menu(name="🔝 回到顶部")
async def back_to_top(interaction: discord.Interaction, message: discord.Message):
    """
    当用户在消息上右键 -> Apps -> 回到顶部 时触发。
    提供一个返回帖子顶部的链接。
    """
    # 检查命令是否在帖子（Thread）中被调用
    if isinstance(interaction.channel, discord.Thread):
        thread = interaction.channel
        
        # 创建一个包含跳转链接的按钮
        view = discord.ui.View()
        button = discord.ui.Button(
            label=f"🚀 点击回到《{thread.name}》顶部",
            style=discord.ButtonStyle.link,
            url=thread.jump_url
        )
        view.add_item(button)

        # 以仅自己可见的方式回复消息
        await interaction.response.send_message(
            content="这是您请求的帖子顶部跳转链接：",
            view=view,
            ephemeral=True
        )
    else:
        # 如果不在帖子中，则发送错误提示
        await interaction.response.send_message(
            "❌ 此命令只能在论坛的帖子内部使用。",
            ephemeral=True
        )

# --- 设置函数 ---
# 由于我们不再使用 Cog，setup 函数现在负责直接将命令添加到 bot 的命令树中。
async def setup(bot: commands.Bot):
    bot.tree.add_command(back_to_top)