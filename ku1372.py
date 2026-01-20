#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
from bs4 import BeautifulSoup
import os
import time
import random
import re
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
import zipfile
from PIL import Image
import io
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.text import Text

console = Console()
download_status = {}
total_albums_count = 0
processed_albums_count = 0

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

session = requests.Session()
session.mount('http://', requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20))
session.mount('https://', requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20))

def update_download_status(album_name, status, progress=0, tag_name="", speed=0):
    global download_status
    download_status[album_name] = {
        'tag': tag_name,
        'status': status,
        'progress': progress,
        'speed': speed
    }

def get_stats_text():
    global total_albums_count, processed_albums_count
    if total_albums_count > 0:
        progress_percent = processed_albums_count / total_albums_count * 100
    else:
        progress_percent = 0
    return f"[bold white on blue]总计: {total_albums_count} 个相册 | 已处理: {processed_albums_count} 个 | 进度: {progress_percent:.1f}%[/bold white on blue]"

def create_status_table():
    table = Table(title="相册下载状态", show_header=True, header_style="bold magenta")
    table.add_column("标签", width=20, style="cyan")
    table.add_column("相册名称", width=40, style="green")
    table.add_column("进度", width=15, style="yellow")
    table.add_column("速度", width=15, style="red")
    table.add_column("状态", width=20, style="blue")
    status_order = {"等待下载": 0, "正在下载": 1, "下载完成": 2, "跳过，本地已存在": 3}
    sorted_albums = sorted(
        download_status.items(),
        key=lambda x: (status_order.get(x[1]['status'], 4), x[0])
    )
    if sorted_albums:
        for album_name, status_info in sorted_albums:
            if status_info['status'] in ["正在下载", "下载完成", "跳过，本地已存在"]:
                progress_str = f"{status_info['progress']:.1f}%"
            else:
                progress_str = "-"
            if status_info['status'] == "正在下载":
                speed = status_info.get('speed', 0)
                if speed < 1024:
                    speed_str = f"{speed:.1f} KB/s"
                else:
                    speed_str = f"{speed/1024:.1f} MB/s"
            else:
                speed_str = "-"
            status_text = status_info['status']
            if status_info['status'] == "下载完成":
                status_text = Text(status_text, style="green bold")
            elif status_info['status'] == "正在下载":
                status_text = Text(status_text, style="yellow bold")
            elif status_info['status'] == "等待下载":
                status_text = Text(status_text, style="blue")
            elif status_info['status'] == "跳过，本地已存在":
                status_text = Text(status_text, style="cyan bold")
            table.add_row(
                status_info['tag'],
                album_name,
                progress_str,
                speed_str,
                status_text
            )
    else:
        table.add_row(
            "等待中",
            "暂无相册信息",
            "-",
            "-",
            "等待下载",
            style="italic dim"
        )
    return table

def get_soup(url):
    try:
        response = session.get(url, headers=HEADERS, timeout=30)
        response.encoding = 'gb2312'
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        console.print(f"[red]获取页面 {url} 失败: {e}[/red]")
        return None

def get_tags():
    tags = []
    url = 'https://www.ku1372.cc/b/tag/'
    soup = get_soup(url)
    if not soup:
        return tags
    ul_list = soup.find_all('ul')
    for ul in ul_list:
        li_list = ul.find_all('li')
        for li in li_list:
            a_tag = li.find('a')
            span_tag = li.find('span')
            if a_tag and span_tag:
                tag_url = a_tag.get('href')
                tag_name = a_tag.text.strip()
                tag_count = span_tag.text.strip()
                tags.append({
                    'name': f"{tag_name} {tag_count}",
                    'url': tag_url
                })
    return tags

def get_albums(tag_url):
    albums = []
    page = 1
    while True:
        if page == 1:
            url = tag_url
        else:
            import re
            tag_id_match = re.search(r'/b/(\d+)/?', tag_url)
            if tag_id_match:
                tag_id = tag_id_match.group(1)
                if tag_url.endswith('/'):
                    url = f"{tag_url}list_{tag_id}_{page}.html"
                else:
                    url = f"{tag_url}/list_{tag_id}_{page}.html"
            else:
                if tag_url.endswith('/'):
                    url = f"{tag_url}list_{page}.html"
                else:
                    url = f"{tag_url}/list_{page}.html"
        soup = get_soup(url)
        if not soup:
            break
        list_div = soup.find('div', class_='m-list')
        if not list_div:
            break
        li_list = list_div.find_all('li')
        if not li_list:
            break
        for li in li_list:
            a_tag = li.find('a')
            if a_tag:
                album_url = a_tag.get('href')
                album_name = a_tag.get('title', '').strip()
                albums.append({
                    'name': album_name,
                    'url': album_url
                })
        page_div = soup.find('div', class_='page')
        if not page_div:
            print(f"未找到分页控件，结束爬取 (第{page}页)")
            break
        next_page = None
        text_matches = page_div.find_all('a')
        for a in text_matches:
            a_text = a.text.strip()
            if a_text in ['下一页', 'ÏÂÒ»Ò³', 'Next', 'next'] or '下一页' in a_text:
                next_page = a
                break
        if not next_page:
            href_matches = page_div.find_all('a', href=re.compile(r'list_'))
            for a in href_matches:
                if 'this-page' not in a.get('class', []):
                    page_match = re.search(r'_([0-9]+)\.html', a.get('href', ''))
                    if page_match:
                        link_page = int(page_match.group(1))
                        if link_page == page + 1:
                            next_page = a
                            break
        if not next_page:
            print(f"未找到下一页链接，结束爬取 (第{page}页)")
            break
        page += 1
        print(f"准备爬取下一页: 第{page}页")
        time.sleep(random.randint(4, 8))
    return albums

def get_download_link(album_url):
    soup = get_soup(album_url)
    if not soup:
        return None
    title_div = soup.find('div', class_='Title111')
    if not title_div:
        return None
    download_a = title_div.find('a', text=re.compile(r'点击打包下载本套图|µã»÷´ò°üÏÂÔØ±¾Ì×Í¼'))
    if download_a:
        return download_a.get('href')
    return None

def extract_zip(zip_path, extract_dir, delete_after=False):
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        print(f"解压成功: {os.path.basename(zip_path)}")
        if delete_after:
            os.remove(zip_path)
            print(f"删除原压缩包: {os.path.basename(zip_path)}")
        return True
    except Exception as e:
        print(f"解压失败 {os.path.basename(zip_path)}: {e}")
        return False

def verify_image(image_path):
    try:
        with Image.open(image_path) as img:
            img.verify()
        return True
    except Exception as e:
        print(f"图像损坏: {image_path} - {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='爬取ku1372网站相册')
    parser.add_argument('--verify', action='store_true', help='启用已存在文件验证')
    parser.add_argument('--max-workers', type=int, default=2, help='最大下载线程数')
    args = parser.parse_args()
    default_path = r"E:\pachong\结果\ku1372"
    save_path = input(f"请输入保存路径（默认：{default_path}）: ").strip()
    if not save_path:
        save_path = default_path
    os.makedirs(save_path, exist_ok=True)
    console.print("\n=== 解压选项设置 ===")
    extract_choice = input("全站下载完成后是否解压压缩包？(y/n，默认y): ").strip().lower()
    should_extract = extract_choice in ['y', '']
    delete_after = False
    if should_extract:
        delete_choice = input("解压完成后是否删除原压缩包？(y/n，默认y): ").strip().lower()
        delete_after = delete_choice in ['y', '']
    tags = get_tags()
    if not tags:
        console.print("[red]获取标签失败，程序退出[/red]")
        return
    console.print(f"\n[bold cyan]共找到 {len(tags)} 个标签[/bold cyan]")
    total_success = 0
    with Live(None, refresh_per_second=2, console=console) as live:
        def render_content():
            from rich.console import Group
            table = create_status_table()
            group = Group(
                get_stats_text(),
                "",
                table
            )
            return group
        live.update(render_content())
        for tag_index, tag in enumerate(tags):
            tag_name = tag['name']
            console.print(f"\n[bold magenta]=== 开始处理标签 {tag_index+1}/{len(tags)}: {tag_name} ===[/bold magenta]")
            tag_dir = os.path.join(save_path, tag_name)
            os.makedirs(tag_dir, exist_ok=True)
            page = 1
            has_more_pages = True
            while has_more_pages:
                console.print(f"[yellow]正在爬取第 {page} 页相册...[/yellow]")
                if page == 1:
                    current_url = tag['url']
                else:
                    tag_id_match = re.search(r'/b/(\d+)/?', tag['url'])
                    if tag_id_match:
                        tag_id = tag_id_match.group(1)
                        if tag['url'].endswith('/'):
                            current_url = f"{tag['url']}list_{tag_id}_{page}.html"
                        else:
                            current_url = f"{tag['url']}/list_{tag_id}_{page}.html"
                    else:
                        console.print(f"[red]无法提取tag_id，跳过第 {page} 页[/red]")
                        break
                soup = get_soup(current_url)
                if not soup:
                    console.print(f"[red]爬取第 {page} 页失败[/red]")
                    break
                list_div = soup.find('div', class_='m-list')
                if not list_div:
                    console.print(f"[red]第 {page} 页未找到相册列表[/red]")
                    break
                li_list = list_div.find_all('li')
                if not li_list:
                    console.print(f"[red]第 {page} 页未找到相册[/red]")
                    break
                console.print(f"[green]第 {page} 页找到 {len(li_list)} 个相册[/green]")
                current_page_albums = []
                for li in li_list:
                    a_tag = li.find('a')
                    if a_tag:
                        album_url = a_tag.get('href')
                        album_name = a_tag.get('title', '').strip()
                        if album_name:
                            current_page_albums.append({
                                'name': album_name,
                                'url': album_url
                            })
                for album in current_page_albums:
                    global total_albums_count
                    total_albums_count += 1
                    update_download_status(album['name'], "等待下载", 0, tag_name)
                    live.update(render_content())
                thread_success_count = 0
                def download_album_wrapper(album, tag_dir, verify=False, tag_name=""):
                    nonlocal thread_success_count
                    album_name = album['name']
                    album_url = album['url']
                    safe_name = re.sub(r'[\\/:*?"<>|]', '_', album_name)
                    save_path = os.path.join(tag_dir, f"{safe_name}.zip")
                    if os.path.exists(save_path):
                        if verify:
                            update_download_status(album_name, "正在下载", 0, tag_name)
                            live.update(render_content())
                            console.print(f"[yellow]验证已存在文件: {safe_name}[/yellow]")
                            if os.path.getsize(save_path) > 1024:
                                update_download_status(album_name, "跳过，本地已存在", 100, tag_name)
                                live.update(render_content())
                                return True
                            else:
                                console.print(f"[orange]文件已存在但无效，重新下载: {safe_name}[/orange]")
                                os.remove(save_path)
                        else:
                            update_download_status(album_name, "跳过，本地已存在", 100, tag_name)
                            live.update(render_content())
                            console.print(f"[green]跳过，本地已存在: {safe_name}[/green]")
                            return True
                    retry_count = 0
                    max_retries = 5
                    while retry_count < max_retries:
                        try:
                            download_url = get_download_link(album_url)
                            if not download_url:
                                update_download_status(album_name, "下载完成", 0, tag_name)
                                live.update(render_content())
                                console.print(f"[red]获取下载链接失败: {album_name}[/red]")
                                return False
                            delay = random.randint(2, 4)
                            console.print(f"[blue]等待 {delay} 秒后下载: {album_name}[/blue]")
                            time.sleep(delay)
                            update_download_status(album_name, "正在下载", 0, tag_name)
                            live.update(render_content())
                            console.print(f"[green]正在下载: {album_name} (尝试 {retry_count+1}/{max_retries})[/green]")
                            response = session.get(download_url, headers=HEADERS, stream=True, timeout=60)
                            response.raise_for_status()
                            total_size = int(response.headers.get('content-length', 0))
                            console.print(f"[cyan]文件大小: {total_size / 1024 / 1024:.2f} MB[/cyan]")
                            downloaded_size = 0
                            start_time = time.time()
                            last_update_time = start_time
                            with open(save_path, 'wb') as f:
                                for chunk in response.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                                        downloaded_size += len(chunk)
                                        current_time = time.time()
                                        elapsed_time = current_time - start_time
                                        if current_time - last_update_time >= 1.0 and total_size > 0:
                                            progress = (downloaded_size / total_size) * 100
                                            if elapsed_time > 0:
                                                speed_kbps = (downloaded_size / elapsed_time) / 1024
                                            else:
                                                speed_kbps = 0
                                            update_download_status(album_name, "正在下载", progress, tag_name, speed_kbps)
                                            live.update(render_content())
                                            last_update_time = current_time
                            final_time = time.time()
                            total_elapsed = final_time - start_time
                            if total_elapsed > 0:
                                avg_speed_kbps = (downloaded_size / total_elapsed) / 1024
                                console.print(f"[cyan]平均下载速度: {avg_speed_kbps:.2f} KB/s[/cyan]")
                            with open(save_path, 'rb') as f:
                                content = f.read(512)
                                if b'<!DOCTYPE html>' in content or b'<html>' in content or b'<head>' in content:
                                    update_download_status(album_name, "等待下载", 0, tag_name)
                                    live.update(render_content())
                                    console.print(f"[red]下载失败，返回HTML错误页: {album_name}[/red]")
                                    os.remove(save_path)
                                    retry_count += 1
                                    time.sleep(random.randint(4, 8))
                                    continue
                            final_size = os.path.getsize(save_path)
                            console.print(f"[cyan]实际下载大小: {final_size / 1024 / 1024:.2f} MB[/cyan]")
                            if final_size < 1024:
                                update_download_status(album_name, "等待下载", 0, tag_name)
                                live.update(render_content())
                                console.print(f"[red]下载失败，文件太小 ({final_size} bytes): {album_name}[/red]")
                                os.remove(save_path)
                                retry_count += 1
                                time.sleep(random.randint(4, 8))
                                continue
                            if total_size > 0 and abs(final_size - total_size) > 1024:
                                update_download_status(album_name, "等待下载", 0, tag_name)
                                live.update(render_content())
                                console.print(f"[red]下载失败，文件大小不匹配 (预期: {total_size}, 实际: {final_size}): {album_name}[/red]")
                                os.remove(save_path)
                                retry_count += 1
                                time.sleep(random.randint(4, 8))
                                continue
                            update_download_status(album_name, "下载完成", 100, tag_name)
                            live.update(render_content())
                            console.print(f"[green]✓ 下载成功: {album_name}[/green]")
                            thread_success_count += 1
                            return True
                        except requests.exceptions.RequestException as e:
                            update_download_status(album_name, "等待下载", 0, tag_name)
                            live.update(render_content())
                            console.print(f"[red]网络请求失败 (尝试 {retry_count+1}/{max_retries}): {e}[/red]")
                            retry_count += 1
                            if os.path.exists(save_path):
                                os.remove(save_path)
                            time.sleep(random.randint(4, 8))
                        except Exception as e:
                            update_download_status(album_name, "等待下载", 0, tag_name)
                            live.update(render_content())
                            console.print(f"[red]下载专辑 {album_name} 失败 (尝试 {retry_count+1}/{max_retries}): {e}[/red]")
                            retry_count += 1
                            if os.path.exists(save_path):
                                os.remove(save_path)
                            time.sleep(random.randint(4, 8))
                    update_download_status(album_name, "下载完成", 0, tag_name)
                    live.update(render_content())
                    console.print(f"[red]✗ 专辑 {album_name} 下载失败，已重试5次[/red]")
                    return False
                console.print(f"[green]使用 {args.max_workers} 个线程下载当前页相册[/green]")
                with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
                    future_to_album = {
                        executor.submit(download_album_wrapper, album, tag_dir, verify=args.verify, tag_name=tag_name): album
                        for album in current_page_albums
                    }
                    for future in as_completed(future_to_album):
                        album = future_to_album[future]
                        try:
                            future.result()
                        except Exception as e:
                            console.print(f"[red]处理相册 {album['name']} 时发生异常: {e}[/red]")
                        global processed_albums_count
                        processed_albums_count += 1
                        live.update(render_content())
                total_success += thread_success_count
                time.sleep(random.randint(2, 5))
                page_div = soup.find('div', class_='page')
                if not page_div:
                    console.print(f"[yellow]第 {page} 页未找到分页控件，结束该标签爬取[/yellow]")
                    has_more_pages = False
                    break
                next_page = None
                text_matches = page_div.find_all('a')
                for a in text_matches:
                    a_text = a.text.strip()
                    if a_text in ['下一页', 'ÏÂÒ»Ò³', 'Next', 'next'] or '下一页' in a_text:
                        next_page = a
                        break
                if not next_page:
                    console.print(f"[yellow]第 {page} 页未找到下一页链接，结束该标签爬取[/yellow]")
                    has_more_pages = False
                    break
                page += 1
                console.print(f"[cyan]准备爬取下一页: 第 {page} 页[/cyan]")
                time.sleep(random.randint(4, 8))
            console.print(f"[bold magenta]=== 标签 {tag_index+1}/{len(tags)} 处理完成 ===[/bold magenta]")
    if should_extract:
        console.print("\n[bold blue]=== 开始解压压缩包 ===[/bold blue]")
        for tag in tags:
            tag_dir = os.path.join(save_path, tag['name'])
            if os.path.exists(tag_dir):
                for file in os.listdir(tag_dir):
                    if file.endswith('.zip'):
                        zip_path = os.path.join(tag_dir, file)
                        extract_zip(zip_path, tag_dir, delete_after)
    console.print(f"\n[bold green]=== 下载完成 ===[/bold green]")
    console.print(f"[cyan]总标签数: {len(tags)}[/cyan]")
    console.print(f"[green]总成功下载数: {total_success}[/green]")
    console.print(f"[yellow]总处理相册数: {processed_albums_count}[/yellow]")

if __name__ == "__main__":
    main()