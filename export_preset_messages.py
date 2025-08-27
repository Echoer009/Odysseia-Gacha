import sqlite3
import json
import os

DB_FILE = 'posts.db'
OUTPUT_FILE = 'exported_preset_messages.json'

def export_preset_messages():
    """
    从 SQLite 数据库中导出所有预设消息到 JSON 文件。
    """
    if not os.path.exists(DB_FILE):
        print(f"错误: 数据库文件 '{DB_FILE}' 不存在。")
        return

    con = None
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        
        # 查询所有预设消息
        cur.execute("SELECT guild_id, name, content, creator_id FROM preset_messages")
        rows = cur.fetchall()

        if not rows:
            print("数据库中没有找到预设消息。")
            return

        # 将数据转换为字典列表
        exported_data = []
        for row in rows:
            exported_data.append({
                "guild_id": row[0],
                "name": row[1],
                "content": row[2],
                "creator_id": row[3]
            })

        # 将数据写入 JSON 文件
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(exported_data, f, ensure_ascii=False, indent=4)
        
        print(f"成功导出 {len(exported_data)} 条预设消息到 '{OUTPUT_FILE}'。")

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
    export_preset_messages()