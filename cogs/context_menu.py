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
        # 场景一：在帖子内，提供回到帖子顶部的链接
        thread = interaction.channel
        view = discord.ui.View()
        button = discord.ui.Button(
            label=f"🚀 点击回到《{thread.name}》顶部",
            style=discord.ButtonStyle.link,
            url=f"{thread.jump_url}/0"
        )
        view.add_item(button)
        await interaction.response.send_message(
            content="这是您请求的帖子顶部跳转链接：",
            view=view,
            ephemeral=True
        )
    elif isinstance(interaction.channel, discord.TextChannel):
        # 场景二：在普通文本频道，提供跳转到频道最顶部的链接
        try:
            # 尝试获取频道的第一条消息
            first_message = [msg async for msg in interaction.channel.history(limit=1, oldest_first=True)][0]
            view = discord.ui.View()
            button = discord.ui.Button(
                label=f"🚀 点击回到 #{interaction.channel.name} 的开头",
                style=discord.ButtonStyle.link,
                url=first_message.jump_url
            )
            view.add_item(button)
            await interaction.response.send_message(
                content="这是您请求的频道顶部跳转链接：",
                view=view,
                ephemeral=True
            )
        except (IndexError, discord.Forbidden):
            # 如果频道为空或没有权限读取历史消息
            await interaction.response.send_message(
                "❌ 无法获取该频道的起始消息（可能为空或权限不足）。",
                ephemeral=True
            )
    else:
        # 其他情况（例如私信、语音频道文本区等）
        await interaction.response.send_message(
            "❌ 此命令仅支持在服务器的帖子或文本频道中使用。",
            ephemeral=True
        )

# --- 设置函数 ---
# 由于我们不再使用 Cog，setup 函数现在负责直接将命令添加到 bot 的命令树中。
async def setup(bot: commands.Bot):
    bot.tree.add_command(back_to_top)