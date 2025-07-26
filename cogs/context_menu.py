# cogs/context_menu.py
import discord
from discord.ext import commands
from discord import app_commands
import os

# --- 最终方案：混合模式 ---

# 1. 保留右键菜单，服务于有权限的用户
@app_commands.context_menu(name="🔝 回到顶部")
async def back_to_top_context_menu(interaction: discord.Interaction, message: discord.Message):
    """
    右键菜单命令，仅在用户有发言权限时能成功响应。
    """
    # 权限检查：Discord API 会在入口处自动处理，如果用户无权，交互会直接失败。
    # 因此，能执行到这里的，都是有权限的用户。
    
    view = discord.ui.View()
    try:
        if isinstance(interaction.channel, discord.Thread):
            thread = interaction.channel
            button = discord.ui.Button(label=f"🚀 点击回到《{thread.name}》顶部", style=discord.ButtonStyle.link, url=f"{thread.jump_url}/0")
            view.add_item(button)
        elif isinstance(interaction.channel, discord.TextChannel):
            channel = interaction.client.get_channel(interaction.channel.id)
            first_message = [msg async for msg in channel.history(limit=1, oldest_first=True)][0]
            button = discord.ui.Button(label=f"🚀 点击回到 #{interaction.channel.name} 的开头", style=discord.ButtonStyle.link, url=first_message.jump_url)
            view.add_item(button)
        else:
            await interaction.response.send_message("❌ 此命令仅支持在服务器的帖子或文本频道中使用。", ephemeral=True)
            return
    except (discord.Forbidden, IndexError):
        await interaction.response.send_message("❌ 无法获取该频道的起始消息（可能为空或我没有读取历史的权限）。", ephemeral=True)
        return

    # 对于有权限的用户，发送临时的、仅自己可见的消息
    await interaction.response.send_message(content="这是您请求的跳转链接：", view=view, ephemeral=True)


# 2. 新增一个 Cog 来处理基于表情回应的备用方案
class BackToTopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.trigger_emoji = "🆙"

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # 忽略机器人自己的回应
        if payload.user_id == self.bot.user.id:
            return
        
        # 检查表情是否是我们约定的触发器
        if str(payload.emoji) != self.trigger_emoji:
            return

        # --- 简化逻辑：不检查权限，直接响应 ---
        try:
            channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
            if not (isinstance(channel, discord.TextChannel) or type(channel) is discord.Thread):
                return
            
            message = await channel.fetch_message(payload.message_id)
            user = self.bot.get_user(payload.user_id) or await self.bot.fetch_user(payload.user_id)

            # 准备跳转链接
            view = discord.ui.View()
            if isinstance(channel, discord.Thread):
                button = discord.ui.Button(label=f"🚀 点击回到《{channel.name}》顶部", style=discord.ButtonStyle.link, url=f"{channel.jump_url}/0")
                view.add_item(button)
            elif isinstance(channel, discord.TextChannel):
                first_message = [msg async for msg in channel.history(limit=1, oldest_first=True)][0]
                button = discord.ui.Button(label=f"🚀 点击回到 #{channel.name} 的开头", style=discord.ButtonStyle.link, url=first_message.jump_url)
                view.add_item(button)
            
            # 发送公开的、自动删除的消息
            await channel.send(
                content=f"{user.mention} 这是您请求的跳转链接：",
                view=view,
                delete_after=20
            )
            
            # 移除用户的回应
            await message.remove_reaction(payload.emoji, user)

        except (discord.Forbidden, discord.NotFound, IndexError, discord.HTTPException):
            # 如果遇到任何权限、找不到对象、网络等问题，都静默失败，不响应也不报错
            pass


# --- 设置函数 ---
async def setup(bot: commands.Bot):
    # 将右键菜单命令添加到树
    bot.tree.add_command(back_to_top_context_menu)
    # 将包含事件监听器的 Cog 添加到 bot
    await bot.add_cog(BackToTopCog(bot))