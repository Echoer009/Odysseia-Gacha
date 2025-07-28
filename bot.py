# bot.py
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
from typing import Literal, Optional
import json

# --- 初始化 ---
# 加载 .env 文件中的环境变量
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_IDS_STR = os.getenv("GUILD_IDS")
ALLOWED_CHANNEL_IDS_STR = os.getenv("ALLOWED_CHANNEL_IDS", "")
DELIVERY_CHANNEL_ID_STR = os.getenv("DELIVERY_CHANNEL_ID", "")
DEFAULT_POOL_EXCLUSION_IDS_STR = os.getenv("DEFAULT_POOL_EXCLUSION_IDS", "")

# --- Bot 设置 ---
# 创建一个 Bot 实例，并启用所有默认的 Intents
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.members = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        # --- 统一配置处理 ---
        self.allowed_forum_ids = {int(cid.strip()) for cid in ALLOWED_CHANNEL_IDS_STR.split(',') if cid.strip()}
        self.delivery_channel_id = int(DELIVERY_CHANNEL_ID_STR) if DELIVERY_CHANNEL_ID_STR else None
        self.default_pool_exclusions = {int(cid.strip()) for cid in DEFAULT_POOL_EXCLUSION_IDS_STR.split(',') if cid.strip()}

    async def setup_hook(self):
        """
        这个函数会在机器人登录时被调用，用于加载 Cogs 和同步命令。
        """
        # --- 加载 Cogs ---
        print("--- 正在加载 Cogs ---")
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and not filename.startswith('__'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    print(f"✅ 已加载 Cog: {filename}")
                except Exception as e:
                    print(f"❌ 加载 Cog {filename} 失败: {e}")
        print("--- 所有 Cogs 加载完毕 ---")
        
        # --- 自动同步应用程序命令 ---
        # --- 自动同步应用程序命令 ---
        guild_ids = {int(gid.strip()) for gid in GUILD_IDS_STR.split(',') if gid.strip()} if GUILD_IDS_STR else set()

        if guild_ids:
            print(f"--- 检测到指定服务器，将以【服务器命令】模式运行 ---")
            # 1. 将所有在代码中定义的命令复制到指定的服务器
            for guild_id in guild_ids:
                guild = discord.Object(id=guild_id)
                try:
                    print(f"  - 正在同步到服务器 ID: {guild_id}...")
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    print(f"  - ✅ 服务器 {guild_id} 同步完成。")
                except Exception as e:
                    print(f"  - ❌ 服务器 {guild_id} 同步失败: {e}")
            
            # 2. 清空所有全局命令，这是解决指令重复的关键
            print("--- 正在清空全局命令以防止重复... ---")
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            print("--- ✅ 全局命令已清空。机器人现在只会在指定服务器显示命令。 ---")

        else:
            # 如果没有在 .env 指定服务器，则作为全局命令运行
            print("--- 未检测到指定服务器，将以【全局命令】模式运行 (同步可能需要长达1小时)... ---")
            await self.tree.sync()
            print("--- ✅ 全局命令同步完成 ---")


    async def on_ready(self):
        """
        当机器人准备就绪时调用。
        """
        print(f'🚀 {self.user} 已成功登录并准备就绪!')
        print(f'机器人ID: {self.user.id}')
        print(f'监控服务器数量: {len(self.guilds)}')

        # --- 打印 .env 配置信息 ---
        print("\n--- 正在加载 .env 配置 ---")
        if self.allowed_forum_ids:
            print(f"✅ 成功加载 {len(self.allowed_forum_ids)} 个监控论坛频道:")
            # 为了美观，我们尝试获取频道名称
            for channel_id in self.allowed_forum_ids:
                channel = self.get_channel(channel_id)
                if channel:
                    print(f"  - {channel.name} (ID: {channel_id})")
                else:
                    print(f"  - 未找到频道 (ID: {channel_id})")
        else:
            print("⚠️ 未在 .env 文件中找到或加载任何 ALLOWED_CHANNEL_IDS。")

        if self.delivery_channel_id:
            channel = self.get_channel(self.delivery_channel_id)
            if channel:
                print(f"✅ 速递频道已设置为: {channel.name} (ID: {self.delivery_channel_id})")
            else:
                print(f"⚠️ 未找到速递频道 (ID: {self.delivery_channel_id})")
        else:
            print("ℹ️ 未在 .env 文件中配置速递频道 (DELIVERY_CHANNEL_ID)。")
        print("--- .env 配置加载完毕 ---\n")


        # --- 打印已注册的命令 ---
        print("--- 正在获取已注册的命令列表 ---")
        guild_ids_str = os.getenv("GUILD_IDS")
        cmd_list = []
        location = "全局"
        
        # 检查是否有指定的服务器ID
        if guild_ids_str:
            # 只选择第一个服务器作为代表来获取命令列表，避免启动时多次请求
            first_guild_id = int(guild_ids_str.split(',')[0].strip())
            guild = discord.Object(id=first_guild_id)
            cmd_list = await self.tree.fetch_commands(guild=guild)
            location = f"指定的服务器 (以 {first_guild_id} 为代表)"
        else:
            # 如果没有指定服务器，则获取全局命令
            cmd_list = await self.tree.fetch_commands()

        print(f"--- ✅ 在 [{location}] 共找到 {len(cmd_list)} 条命令 ---")
        
        def print_commands(commands, prefix=""):
            for cmd in commands:
                if isinstance(cmd, discord.app_commands.Group):
                    print_commands(cmd.commands, prefix=f"{prefix}{cmd.name} ")
                else:
                    print(f"  - /{prefix}{cmd.name}")

        print_commands(cmd_list)
        print("--- 命令列表打印完毕 ---\n")

        await self.change_presence(activity=discord.Game(name="监控新帖子"))

# --- 手动同步命令 (仅限所有者) ---
@commands.guild_only()
@commands.is_owner()
@commands.command()
async def sync(
  ctx: commands.Context, guilds: commands.Greedy[discord.Object], spec: Optional[Literal["~", "*", "^"]] = None) -> None:
    """
    一个强大的手动同步命令，仅限机器人所有者使用。
    用法:
        !sync         -> 全局同步
        !sync ~       -> 同步当前服务器的命令
        !sync *       -> 将全局命令复制到当前服务器并同步
        !sync ^       -> 清除当前服务器的所有命令并同步
        !sync 123 456 ... -> 同步到指定的一个或多个服务器ID
    """
    if not guilds:
        if spec == "~":
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "*":
            ctx.bot.tree.copy_global_to(guild=ctx.guild)
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "^":
            ctx.bot.tree.clear_commands(guild=ctx.guild)
            await ctx.bot.tree.sync(guild=ctx.guild)
            synced = []
        else:
            synced = await ctx.bot.tree.sync()

        await ctx.send(
            f"同步了 {len(synced)} 条命令到 {'当前服务器' if spec else '全局'}。"
        )
        return

    # --- 处理多个服务器ID的同步 ---
    succeeded = []
    failed = []
    for guild in guilds:
        try:
            # 尝试同步命令到指定的服务器
            await ctx.bot.tree.sync(guild=guild)
            succeeded.append(str(guild.id))
        except discord.HTTPException as e:
            # 如果同步失败 (例如, 机器人不在该服务器), 记录下来
            failed.append(f"{guild.id} (HTTP错误: {e.code})")
        except Exception as e:
            # 捕获其他可能的未知错误
            failed.append(f"{guild.id} (未知错误: {type(e).__name__})")

    # --- 构造清晰的反馈消息 ---
    message_parts = []
    if succeeded:
        message_parts.append(f"✅ 成功同步到 {len(succeeded)} 个服务器: `{', '.join(succeeded)}`")
    if failed:
        message_parts.append(f"❌ 在 {len(failed)} 个服务器上同步失败: `{', '.join(failed)}`")

    # 如果 guilds 列表不为空但没有任何操作成功或失败 (理论上不太可能)
    if not message_parts and guilds:
        await ctx.send("处理了指定的服务器，但没有发生任何变化。")
        return

    await ctx.send("\n".join(message_parts))


# --- 运行 Bot ---
async def main():
    bot = MyBot()
    bot.add_command(sync) # 将 sync 命令添加到 bot
    await bot.start(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    # 确保 .env 文件存在
    if DISCORD_BOT_TOKEN is None:
        print("❌ 错误: 找不到 DISCORD_BOT_TOKEN。")
        print("请确保你的项目根目录下有一个 .env 文件，并且其中包含 'DISCORD_BOT_TOKEN=你的令牌'。")
    else:
        # 在 Windows 上设置正确的事件循环策略
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())