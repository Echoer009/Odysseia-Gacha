# cogs/jump_link_modifier.py
import discord
from discord.ext import commands
import os
import re

class JumpLinkModifierCog(commands.Cog):
    """
    一个专门的Cog，用于监听特定频道中的消息，
    并自动将消息中的Discord链接修改为指向起点的链接。
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 从 .env 文件加载目标频道的ID
        try:
            self.jump_channel_id = int(os.getenv("JUMP_CHANNEL_ID"))
            print(f"[JumpLinkModifier] 已加载回顶区频道 ID: {self.jump_channel_id}")
        except (TypeError, ValueError):
            self.jump_channel_id = None
            print("⚠️ [JumpLinkModifier] 未在 .env 文件中配置 JUMP_CHANNEL_ID，此功能将不会启动。")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 1. 检查功能是否已启用，以及消息是否来自配置的频道
        if not self.jump_channel_id or message.channel.id != self.jump_channel_id:
            return
        
        # 2. 忽略机器人自己发送的消息，防止无限循环
        if message.author == self.bot.user:
            return

        # 3. 使用更宽松的正则表达式查找Discord链接（无论是否包含消息ID）
        link_pattern = r"(https://discord\.com/channels/\d+/\d+)"  # 只匹配服务器和频道ID部分
        match = re.search(link_pattern, message.content)

        if match:
            base_link = match.group(0)

            modified_link = base_link.rstrip('/') + "/0"
            
            # 4. 创建一个包含修改后链接的临时消息
            response_content = (
                f"{message.author.mention} {modified_link}"
            )
            try:
                # 1. 先删除原消息
                await message.delete()
                # 2. 再发送新消息
                await message.channel.send(content=response_content)
            except discord.Forbidden:
                # 如果机器人没有删除消息的权限
                await message.channel.send(
                    f"⚠️ **权限不足**：我需要“管理消息”权限才能删除原链接。\n"
                    f"{message.author.mention} 这是修改后的链接：{modified_link}",
                    delete_after=30
                )
            except Exception as e:
                print(f"[JumpLinkModifier] 处理消息时出错: {e}")


async def setup(bot: commands.Bot):
    """将Cog添加到Bot中。"""
    await bot.add_cog(JumpLinkModifierCog(bot))