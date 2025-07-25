# Odysseia Gacha - Discord 论坛增强机器人

Odysseia Gacha 是一款功能不怎么丰富的 Discord.py 机器人，旨在增强论坛频道的使用体验。它提供了新帖速递、帖子抽卡、快速回复等多种功能。

## ✨ 核心功能

-   **新帖速递**: 自动监控指定的论坛频道，并将新帖子的摘要信息发送到速递频道，方便成员快速了解动态。
-   **论坛抽卡**: 将论坛帖子作为“卡池”，用户可以进行“单抽”或“五连抽”，随机获取帖子链接，增加社区互动性。
-   **权限管理**: 所有管理指令均可通过 `.env` 文件配置特定的用户身份组，实现灵活的权限控制。
-   **预设消息**: 管理员可以创建常用的回复模板。所有成员都可以通过右键菜单快速调用这些预设消息来回复他人，提高沟通效率。
-   **实用工具**: 提供“回到顶部”等便捷的右键菜单工具，优化论坛浏览体验。

## 🚀 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/your-username/Odysseia-dolo.git
cd Odysseia-dolo
```

### 2. 安装依赖

建议在虚拟环境中安装。

```bash
pip install -r requirements.txt
```

### 3. 创建并配置 `.env` 文件

在项目根目录下创建一个名为 `.env` 的文件，并根据以下模板填入您的信息：

```env
# 机器人令牌 (必需)
DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN_HERE

# 你的测试服务器ID (强烈建议，用于快速同步命令)
GUILD_ID=YOUR_TEST_SERVER_ID_HERE

# 管理员身份组ID (多个ID用英文逗号,分隔)
ADMIN_ROLE_IDS=ROLE_ID_1,ROLE_ID_2

# 允许手动全量同步的论坛频道ID (多个ID用英文逗号,分隔)
ALLOWED_CHANNEL_IDS=FORUM_ID_1,FORUM_ID_2

# 预设消息创建者身份组ID (多个ID用英文逗号,分隔)
PRESET_CREATOR_ROLE_IDS=ROLE_ID_3,ROLE_ID_4
```

### 4. 运行机器人

```bash
python bot.py
```

## 📖 指令详情

关于所有指令的详细用法和说明，请参考 `COMMANDS_TUTORIAL.md` 文件。

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 开源协议。