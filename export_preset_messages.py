import sqlite3
import os

DB_FILE = 'posts.db'
OUTPUT_FILE = 'exported_preset_messages.md'

def export_preset_messages_to_md():
    """
    从 SQLite 数据库中导出所有预设消息到 Markdown 文件。
    """
    if not os.path.exists(DB_FILE):
        print(f"错误: 数据库文件 '{DB_FILE}' 不存在。")
        return

    con = None
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        
        # 查询所有预设消息，按服务器ID和名称排序
        cur.execute("SELECT guild_id, name, content, creator_id FROM preset_messages ORDER BY guild_id, name")
        rows = cur.fetchall()

        if not rows:
            print("数据库中没有找到预设消息。")
            return

        # 使用字典来按 guild_id 分组
        grouped_data = {}
        for guild_id, name, content, creator_id in rows:
            if guild_id not in grouped_data:
                grouped_data[guild_id] = []
            grouped_data[guild_id].append({
                "name": name,
                "content": content,
                "creator_id": creator_id
            })

        # 构建 Markdown 字符串
        md_content = "# 预设消息导出\n\n"
        for guild_id, messages in grouped_data.items():
            md_content += f"## 服务器 ID: `{guild_id}`\n\n"
            for msg in messages:
                md_content += f"### `{msg['name']}`\n"
                md_content += f"**创建者 ID:** `{msg['creator_id']}`\n\n"
                md_content += "```\n"
                md_content += f"{msg['content']}\n"
                md_content += "```\n\n"
                md_content += "---\n\n"
        
        # 将数据写入 Markdown 文件
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"成功导出 {len(rows)} 条预设消息到 '{OUTPUT_FILE}'。")

    except sqlite3.Error as e:
        print(f"数据库操作错误: {e}")
    except IOError as e:
        print(f"文件写入错误: {e}")
    except Exception as e:
        print(f"发生未知错误: {e}")
    finally:
        if con:
            con.close()

if __name__ == "__main__":
    export_preset_messages_to_md()