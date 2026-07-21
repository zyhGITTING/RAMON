#!/usr/bin/env python3
"""验证 ERP 托工明细表接口连通性并可取到全量数据。

用法：
    cd "d:/Data Platform Deployment Package7.14"
    python backend/scripts/test_erp_subcontract.py

依赖：
    pip install requests

环境变量（优先从 .env 读取）：
    DATAMID_RAMON_AUTH       必填，Basic Authorization 值
    DATAMID_ERP_SUBCONTRACT_URL  可选，默认使用接口文档地址
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests


DEFAULT_URL = "https://www.ramon.net.cn/api/ramon-api/aiagent/getERPSubcontractDetail"


def load_dotenv(env_path: Path) -> None:
    """简单解析 KEY=VALUE 形式的 .env 文件，不处理引号/转义。"""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def fetch_page(url: str, auth: str, page: int, page_size: int) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": auth,
    }
    payload = {
        "erp_account": "衡阳钢铁",
        "page": page,
        "page_size": page_size,
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30, verify=True)
    response.raise_for_status()
    return response.json()


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")

    auth = os.getenv("DATAMID_RAMON_AUTH", "").strip()
    if not auth:
        print("错误：环境变量 DATAMID_RAMON_AUTH 为空。", file=sys.stderr)
        print("请先在 .env 文件中填写 Ramon 平台提供的 Basic Authorization 值。", file=sys.stderr)
        return 1

    url = os.getenv("DATAMID_ERP_SUBCONTRACT_URL", DEFAULT_URL).strip() or DEFAULT_URL
    page_size = 100
    max_pages = 10000

    print(f"接口地址: {url}")
    print(f"分页大小: {page_size}")
    print("开始拉取...\n")

    all_rows: list[dict] = []
    reported_total: int | None = None
    started_at = time.time()

    for page in range(1, max_pages + 1):
        try:
            body = fetch_page(url, auth, page, page_size)
        except requests.RequestException as exc:
            print(f"第 {page} 页请求失败: {exc}", file=sys.stderr)
            return 1

        code = body.get("code")
        message = body.get("message", "")
        if code != 200:
            print(f"第 {page} 页业务状态码异常: code={code}, message={message}", file=sys.stderr)
            return 1

        data = body.get("data")
        if not isinstance(data, list):
            print(f"第 {page} 页 data 字段不是数组: {type(data)}", file=sys.stderr)
            return 1

        if reported_total is None:
            reported_total = body.get("total")

        all_rows.extend(data)
        has_next = body.get("has_next")
        total_pages = body.get("total_pages")

        print(f"第 {page:>3} 页: 本页 {len(data):>3} 条, 累计 {len(all_rows):>5} 条, total={reported_total}, has_next={has_next}")

        if not data:
            break
        if has_next is False:
            break
        if total_pages is not None and page >= int(total_pages):
            break
        if len(data) < page_size and has_next is not True:
            break

    duration_ms = int((time.time() - started_at) * 1000)

    print("\n" + "=" * 60)
    print(f"接口连通性: OK")
    print(f"接口返回总记录数: {reported_total}")
    print(f"实际拉取记录数: {len(all_rows)}")
    print(f"耗时: {duration_ms} ms")
    if all_rows:
        print(f"首条数据:\n{json.dumps(all_rows[0], ensure_ascii=False, indent=2)}")
        print(f"末条数据:\n{json.dumps(all_rows[-1], ensure_ascii=False, indent=2)}")
    else:
        print("警告：未拉取到任何数据（接口返回空列表）")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
