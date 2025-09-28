# cogs/backup_manager.py
import os
import shutil
import asyncio
import logging
from datetime import datetime, timedelta
from discord.ext import tasks, commands

# --- 日志设置 ---
log = logging.getLogger('discord.backup')
log.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
log.addHandler(handler)

# --- 配置 ---
DB_FILE = 'posts.db'
BACKUP_DIR = 'backups'
BACKUP_RETENTION_DAYS = 7

class BackupManager(commands.Cog):
    """
    管理数据库的自动备份和清理。
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.backup_database.start()

    def cog_unload(self):
        self.backup_database.cancel()

    def _run_backup_and_cleanup(self):
        """
        在同步函数中执行所有阻塞的文件 I/O 操作。
        """
        log.info("--- [同步线程] 开始执行每日数据库备份任务 ---")
        
        try:
            # 确保备份目录存在
            if not os.path.exists(BACKUP_DIR):
                os.makedirs(BACKUP_DIR)
                log.info(f"创建备份目录: {BACKUP_DIR}")

            # 1. 执行备份
            source_path = DB_FILE
            if not os.path.exists(source_path):
                log.warning(f"数据库文件 '{source_path}' 不存在，跳过本次备份。")
                return

            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            backup_filename = f"backup_{timestamp}.db"
            destination_path = os.path.join(BACKUP_DIR, backup_filename)
            
            shutil.copy2(source_path, destination_path)
            log.info(f"✅ 数据库成功备份到: {destination_path}")

        except Exception as e:
            log.error(f"❌ 数据库备份失败: {e}", exc_info=True)

        # 2. 清理旧备份
        try:
            cutoff_date = datetime.now() - timedelta(days=BACKUP_RETENTION_DAYS)
            files_deleted = 0
            if os.path.exists(BACKUP_DIR):
                for filename in os.listdir(BACKUP_DIR):
                    if filename.startswith('backup_') and filename.endswith('.db'):
                        file_path = os.path.join(BACKUP_DIR, filename)
                        try:
                            timestamp_str = filename.replace('backup_', '').replace('.db', '')
                            file_date = datetime.strptime(timestamp_str, '%Y-%m-%d_%H-%M-%S')
                            
                            if file_date < cutoff_date:
                                os.remove(file_path)
                                log.info(f"🗑️ 已删除旧备份文件: {filename}")
                                files_deleted += 1
                        except (ValueError, IndexError):
                            log.warning(f"无法解析备份文件的时间戳，已跳过: {filename}")
                            continue
                if files_deleted > 0:
                    log.info(f"清理任务完成，共删除了 {files_deleted} 个旧备份。")
                else:
                    log.info("没有需要清理的旧备份。")
            else:
                log.info("备份目录不存在，跳过清理。")

        except Exception as e:
            log.error(f"❌ 清理旧备份时发生错误: {e}", exc_info=True)
        
        log.info("--- [同步线程] 每日数据库备份任务执行完毕 ---")

    @tasks.loop(hours=24)
    async def backup_database(self):
        """
        每天执行一次的数据库备份和清理任务（异步包装器）。
        """
        await asyncio.to_thread(self._run_backup_and_cleanup)

    @backup_database.before_loop
    async def before_backup_loop(self):
        log.info("备份任务循环正在等待机器人准备就绪...")
        await self.bot.wait_until_ready()

# --- Cog 设置函数 ---
async def setup(bot: commands.Bot):
    await bot.add_cog(BackupManager(bot))