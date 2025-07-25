# bot.py
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
from typing import Literal, Optional

# --- 初始化 ---
# 加载 .env 文件中的环境变量
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
# 加载测试服务器ID，用于快速同步
GUILD_ID = os.getenv("GUILD_ID") 

# --- Bot 设置 ---
# 创建一个 Bot 实例，并启用所有默认的 Intents
# 为了 on_thread_create 和 on_message 事件，members 和 message_content intents 是必需的
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True  # 需要在开发者门户中启用
intents.members = True # 需要在开发者门户中启用

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """
        这个函数会在机器人登录时被调用，用于加载 Cogs 和同步命令。
        """
        print("--- 正在加载 Cogs ---")
        # 遍历 cogs 文件夹并加载所有 .py 文件
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and not filename.startswith('__'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    print(f"✅ 已加载 Cog: {filename}")
                except Exception as e:
                    print(f"❌ 加载 Cog {filename} 失败: {e}")
        print("--- 所有 Cogs 加载完毕 ---")
        
        # --- 自动同步应用程序命令 ---
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            print(f"--- 正在向测试服务器 (ID: {GUILD_ID}) 同步命令... ---")
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print("--- ✅ 测试服务器命令同步完成 ---")
        else:
            print("--- 正在进行全局命令同步 (可能需要长达1小时)... ---")
            await self.tree.sync()
            print("--- ✅ 全局命令同步完成 ---")


    async def on_ready(self):
        """
        当机器人准备就绪时调用。
        """
        print(f'🚀 {self.user} 已成功登录并准备就绪!')
        print(f'机器人ID: {self.user.id}')
        print(f'监控服务器数量: {len(self.guilds)}')
        # --- 新增：打印已注册的命令 ---
        print("\n--- 正在获取已注册的命令列表 ---")
        guild_id = os.getenv("GUILD_ID")
        cmd_list = []
        location = "全局"
        if guild_id:
            # 如果提供了测试服务器ID，就从该服务器获取命令
            guild = discord.Object(id=int(guild_id))
            cmd_list = await self.tree.fetch_commands(guild=guild)
            location = f"测试服务器 (ID: {guild_id})"
        else:
            # 否则获取全局命令
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
        !sync 123 456 -> 同步到指定的服务器ID
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

    ret = 0
    for guild in guilds:
        try:
            await ctx.bot.tree.sync(guild=guild)
        except discord.HTTPException:
            pass
        else:
            ret += 1

    await ctx.send(f"已同步到 {ret}/{len(guilds)} 个服务器。")


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