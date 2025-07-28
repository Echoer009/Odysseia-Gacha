# cogs/context_menu.py
import discord
from discord.ext import commands
from discord import app_commands
import os

# --- 辅助函数：安全地截断标签文本 ---
def truncate_label(text: str, max_length: int = 80) -> str:
    """如果文本超过最大长度，则截断并添加省略号。"""
    if len(text) > max_length:
        return text[:max_length - 3] + "..."
    return text

# --- 右键菜单命令 ---
@app_commands.context_menu(name="🔝 回到顶部")
async def back_to_top_context_menu(interaction: discord.Interaction, message: discord.Message):
    """右键菜单命令，通过 defer 和完整的错误处理确保响应。"""
    await interaction.response.defer(ephemeral=True)
    
    try:
        view = discord.ui.View()
        
        if isinstance(interaction.channel, discord.Thread):
            thread = interaction.channel
            label = truncate_label(f"🚀 点击回到《{thread.name}》顶部")
            # 修复了跳转链接，使其指向帖子的第一条消息
            jump_url = f"https://discord.com/channels/{thread.guild.id}/{thread.id}/0"
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=jump_url)
            view.add_item(button)
            
        elif isinstance(interaction.channel, discord.TextChannel):
            channel = interaction.channel
            label = truncate_label(f"🚀 点击回到 #{channel.name} 的开头")
            jump_url = f"https://discord.com/channels/{channel.guild.id}/{channel.id}/0"
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=jump_url)
            view.add_item(button)
            
        else:
            await interaction.followup.send("❌ 此命令仅支持在服务器的帖子或文本频道中使用。", ephemeral=True)
            return
            
        await interaction.followup.send(content="这是您请求的跳转链接：", view=view, ephemeral=True)

    except Exception as e:
        print(f"执行 '回到顶部' 命令时发生错误: {e}")
        if not interaction.response.is_done():
            await interaction.followup.send("❌ 处理您的请求时发生了一个未知错误，请稍后再试。", ephemeral=True)

# --- 基于表情回应的备用方案 ---
class BackToTopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.trigger_emoji = "🆙"

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        if str(payload.emoji) != self.trigger_emoji:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                return
            
            message = await channel.fetch_message(payload.message_id)
            user = self.bot.get_user(payload.user_id) or await self.bot.fetch_user(payload.user_id)

            view = discord.ui.View()
            if isinstance(channel, discord.Thread):
                label = truncate_label(f"🚀 点击回到《{channel.name}》顶部")
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=f"{channel.jump_url}/0")
                view.add_item(button)
            elif isinstance(channel, discord.TextChannel):
                label = truncate_label(f"🚀 点击回到 #{channel.name} 的开头")
                jump_url = f"https://discord.com/channels/{channel.guild.id}/{channel.id}/0"
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=jump_url)
                view.add_item(button)
            
            await channel.send(
                content=f"{user.mention} 这是您请求的跳转链接：",
                view=view,
                delete_after=20
            )
            await message.remove_reaction(payload.emoji, user)

        except (discord.Forbidden, discord.NotFound, IndexError, discord.HTTPException):
            pass


# --- 设置函数 ---
async def setup(bot: commands.Bot):
    # 将右键菜单命令添加到树
    bot.tree.add_command(back_to_top_context_menu)
    # 将包含事件监听器的 Cog 添加到 bot
    await bot.add_cog(BackToTopCog(bot))