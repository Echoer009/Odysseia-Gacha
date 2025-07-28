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
    右键菜单命令，通过 defer 和完整的错误处理确保响应。
    """
    # 立即响应交互，防止超时
    await interaction.response.defer(ephemeral=True)
    
    try:
        view = discord.ui.View()
        
        if isinstance(interaction.channel, discord.Thread):
            thread = interaction.channel
            # 帖子可以直接用 jump_url 获取顶部链接
            first_message_url = thread.jump_url
            button = discord.ui.Button(label=f"🚀 点击回到《{thread.name}》顶部", style=discord.ButtonStyle.link, url=first_message_url)
            view.add_item(button)
            
        elif isinstance(interaction.channel, discord.TextChannel):
            channel = interaction.channel
            # 对于普通频道，我们直接跳转到被右键的消息，因为无法保证能获取到第一条消息
            jump_url = f"https://discord.com/channels/{channel.guild.id}/{channel.id}/0"
            button = discord.ui.Button(label=f"🚀 点击回到 #{channel.name} 的开头", style=discord.ButtonStyle.link, url=jump_url)
            view.add_item(button)
            
        else:
            await interaction.followup.send("❌ 此命令仅支持在服务器的帖子或文本频道中使用。", ephemeral=True)
            return
            
        # 使用 followup 发送最终结果
        await interaction.followup.send(content="这是您请求的跳转链接：", view=view, ephemeral=True)

    except Exception as e:
        # 捕获所有未预料到的错误，并向用户报告
        print(f"执行 '回到顶部' 命令时发生错误: {e}")
        # 确保即使出错也有响应
        if not interaction.response.is_done():
            await interaction.followup.send("❌ 处理您的请求时发生了一个未知错误，请稍后再试。", ephemeral=True)


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
                jump_url = f"https://discord.com/channels/{channel.guild.id}/{channel.id}/0"
                button = discord.ui.Button(label=f"🚀 点击回到 #{channel.name} 的开头", style=discord.ButtonStyle.link, url=jump_url)
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