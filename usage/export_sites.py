"""
把后端爬虫注册表导出为前端站点配置 docs/js/sites-data.js

前端「找本子」站点按钮、提交页站点下拉、站点徽章颜色全部以这份数据渲染，
保证前后端站点列表永远一致。注册表变动后跑一次本脚本即可：
    python usage/export_sites.py
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from crawlers import registry  # noqa: E402

DEST = os.path.join(SCRIPT_DIR, '..', 'docs', 'js', 'sites-data.js')

TEMPLATE = """// 本文件由 usage/export_sites.py 自动生成，请勿手改
// 数据源：usage/crawlers/registry.py 注册的站点
window.SITES = {sites};
"""


def main():
    sites = registry.registered_sites()
    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    with open(DEST, 'w', encoding='utf-8') as f:
        f.write(TEMPLATE.format(sites=json.dumps(sites, ensure_ascii=False, indent=2)))
    print(f'已导出 {len(sites)} 个站点到 {os.path.abspath(DEST)}')
    for s in sites:
        print(f"  {s['key']:<10} {s['name']}")


if __name__ == '__main__':
    main()
