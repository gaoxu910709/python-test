#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
凸凹吧 (https://www.tuao.cc/) 相册爬虫

功能:
  - 爬取网站的图集
  - 图像内容验证：自动检测并重新下载损坏的图片 (需 Pillow 库)

流程:
  主页 -> 遍历当前列表页 -> 提取专辑链接 (先收集) -> 检查当前页面下有无下一页（若有则重复遍历、提取、检查和进入下一页，若无下一页则下一步）
      └-> (并发处理) -> (延迟) -> 进入专辑页 -> 获取专辑内总页数
           └-> 遍历专辑内所有分页 -> 提取图片链接
                └-> (并发下载) -> 验证图片内容 -> 下载图片并保存

特点:
  - 两级并发: 并发处理多个图集，同时在每个图集内部并发下载图片
  - 图像验证:
    - 通过 --verify 启用，可识别并修复已存在的损坏文件
    - 验证新下载的内容，防止 HTML 错误页被保存
  - 防护增强:
    - 增加连接池 (HTTPAdapter)
    - 增加列表页爬取延迟 (page-sleep)
    - 新增专辑详情页请求延迟 (album-sleep) 以防止 429
  - 原子化写入/断点续传: 自动跳过已存在的图片文件
  - 健壮的解析: 使用更稳定的CSS选择器，并有清晰的日志记录
  - 统一日志: 实现了 [专辑 X/N] 和 (图片 Y/Z) 进度打印
  - 随机加入4~8s延迟，模拟用户真实操作
  - 重试机制：如遇链接或者下载失败时，重试5次，每次重试前随机延时4~8秒，5次失败后暂停脚本等待，待用户检查网络后按任意键继续链接或下载，以此循环
"""

import os
import re
import time
import random
import argparse
import logging
import io
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Dict, Optional, Set, Any

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from bs4 import BeautifulSoup

# -------- 检查 Pillow 库 --------
try:
    from PIL import Image, ImageFile
    from PIL.Image import UnidentifiedImageError
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    Image.MAX_IMAGE_PIXELS = None
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

# -------- 默认配置 (针对反爬优化) --------
BASE_URL = "https://www.tuao.cc/"
# 分类列表
CATEGORIES = [
    ("最新", "/Articles"),
    ("无圣光", "/Articles/Categories/1"),
    ("凸凹图", "/Articles/Categories/2"),
    ("靓人体", "/Articles/Categories/3"),
    ("写真集", "/Articles/Categories/4")
]
DEFAULT_SAVE_DIR = r"E:\pachong\结果\凹凸吧"
DEFAULT_RETRIES = 5
DEFAULT_TIMEOUT = 20
DEFAULT_CONCURRENCY_ALBUM = 5      # 并发处理专辑数量
DEFAULT_CONCURRENCY_IMAGE = 8      # 专辑内部并发下载图片数量
DEFAULT_PAGE_SLEEP_MIN = 4.0       # 列表页爬取延迟最小值
DEFAULT_PAGE_SLEEP_MAX = 8.0       # 列表页爬取延迟最大值
DEFAULT_ALBUM_SLEEP_MIN = 4.0      # 专辑详情页请求延迟最小值
DEFAULT_ALBUM_SLEEP_MAX = 8.0      # 专辑详情页请求延迟最大值
DEFAULT_POOL_SIZE = 64             # 连接池大小

# -------- 日志设置 --------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if not PILLOW_AVAILABLE:
    logging.warning("Pillow 库未安装。将跳过严格的图像完整性校验 (请运行: pip install Pillow)")

# -------- 辅助函数 --------
def make_session() -> requests.Session:
    """创建并配置requests.Session，增加连接池大小。"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    })
    
    # 配置连接池适配器
    adapter = HTTPAdapter(
        pool_connections=DEFAULT_POOL_SIZE,  # 连接池的最大数量
        pool_maxsize=DEFAULT_POOL_SIZE       # 保持活动的连接数
    )
    # 为 http 和 https 协议都挂载这个适配器
    s.mount('http://', adapter)
    s.mount('https://', adapter)
    
    return s

def get_random_delay(min_delay: float, max_delay: float) -> float:
    """获取指定范围内的随机延迟"""
    return random.uniform(min_delay, max_delay)

def request_with_retry(session: requests.Session, url: str, retries: int, timeout: int, is_binary: bool = False) -> Optional[Any]:
    """带重试机制的请求函数"""
    r: Optional[requests.Response] = None
    for attempt in range(1, retries + 1):
        try:
            if is_binary:
                r = session.get(url, timeout=timeout, stream=True)
            else:
                r = session.get(url, timeout=timeout)
            r.raise_for_status() # 检查 4xx/5xx 错误
            return r.content if is_binary else r.text
            
        except RequestException as e:
            wait_time = get_random_delay(4.0, 8.0)
            status_msg = f"{r.status_code}" if r is not None else "无响应"

            if attempt < retries:
                logging.warning("请求失败: %s (尝试 %d/%d) 错误: %s。状态: %s，等待 %.1fs 并重试。", 
                                url, attempt, retries, e, status_msg, wait_time)
                time.sleep(wait_time)
            else:
                logging.error("请求失败: %s (所有尝试均失败)。错误: %s", url, e)
                # 5次失败后暂停脚本等待
                input("按任意键继续...")
    return None

def sanitize_filename(name: str, maxlen: int = 150) -> str:
    """清洗并截断文件名/文件夹名。"""
    if not name: return "untitled"
    s = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return s[:maxlen] or "untitled"

# -------- 图像验证辅助函数 --------
def is_image_valid_bytes(data: bytes, verify: bool) -> bool:
    """检查内存中的 bytes 是否为有效图像。"""
    if not verify or not PILLOW_AVAILABLE:
        return True
    
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify() # 检查文件是否截断或损坏
        return True
    except (UnidentifiedImageError, ValueError, OSError, TypeError) as e:
        logging.debug("Pillow 校验失败 (内容确认损坏或为HTML): %s", e)
        return False
    except Exception as e:
        logging.error("验证时发生系统级异常: %s", e)
        return False

def is_image_valid_file(filepath: str, verify: bool) -> bool:
    """检查磁盘上的文件是否为有效图像。"""
    if not verify or not PILLOW_AVAILABLE:
        return True
    
    try:
        with Image.open(filepath) as img:
            img.verify() # 检查文件是否截断或损坏
        return True
    except (UnidentifiedImageError, ValueError, OSError, TypeError, FileNotFoundError) as e:
        logging.debug("Pillow 校验失败 (文件确认损坏或IO问题): %s. 错误: %s", filepath, e)
        return False
    except Exception as e:
        logging.error("校验文件 %s 时发生未知异常: %s", filepath, e)
        return False

def save_bytes_atomic(path: str, data: bytes) -> bool:
    """原子化写入文件，避免文件损坏。"""
    tmp_path = path + ".part"
    try:
        with open(tmp_path, "wb") as f: 
            f.write(data)
        os.replace(tmp_path, path)
        return True
    except IOError as e:
        logging.error("文件写入失败 %s : %s", path, e)
        if os.path.exists(tmp_path):
            try: 
                os.remove(tmp_path)
            except OSError: 
                pass
        return False

# -------- 解析函数 --------
def parse_next_page(soup: BeautifulSoup) -> Optional[str]:
    """从当前页面解析出下一页链接"""
    pagination = soup.find("ul", class_="pagination")
    if not pagination:
        return None
    
    # 查找下一页链接的几种方法
    next_page = None
    
    # 方法1: 查找包含右箭头符号的a标签
    next_page = pagination.find("a", string="»")
    if next_page and "href" in next_page.attrs:
        return next_page["href"]
    
    # 方法2: 查找包含'下一页'文本的a标签
    next_page = pagination.find("a", string=lambda text: text and "下一页" in text)
    if next_page and "href" in next_page.attrs:
        return next_page["href"]
    
    # 方法3: 查找pagination中的最后一个a标签
    all_a = pagination.find_all("a")
    if all_a:
        last_a = all_a[-1]
        if "href" in last_a.attrs:
            href = last_a["href"]
            # 确保不是当前页链接
            if "Page=" in href:
                return href
    
    return None

def parse_albums_on_listing_page(html: str, base_url: str) -> List[Tuple[str, str]]:
    """从列表页HTML中解析出(标题, URL)元组列表。"""
    soup = BeautifulSoup(html, "html.parser")
    albums = []
    
    logging.info("开始解析列表页专辑")
    
    # 简化的专辑提取逻辑：直接查找所有符合条件的a标签对
    # 查找所有class为index-imgcontent-img的a标签（图片链接）
    img_links = soup.find_all("a", class_="index-imgcontent-img")
    logging.info(f"找到 {len(img_links)} 个class为index-imgcontent-img的a标签")
    
    # 查找所有class为index-imgcontent-title的a标签（标题链接）
    title_links = soup.find_all("a", class_="index-imgcontent-title")
    logging.info(f"找到 {len(title_links)} 个class为index-imgcontent-title的a标签")
    
    # 遍历图片链接，尝试为每个图片链接找到对应的标题
    for i, img_link in enumerate(img_links):
        # 获取图片链接的href
        img_href = img_link.get("href")
        if not img_href:
            continue
        
        # 构建完整的专辑URL
        album_url = urljoin(base_url, img_href)
        
        # 查找对应的标题
        album_title = ""
        
        # 方法1: 如果标题链接数量匹配，直接对应
        if i < len(title_links):
            album_title = title_links[i].get_text(strip=True)
        
        # 方法2: 如果方法1失败，查找图片链接的父元素中的标题
        if not album_title:
            # 查找图片链接的父div
            parent_div = img_link.find_parent("div")
            if parent_div:
                # 在父div中查找标题a标签
                title_elem = parent_div.find("a", class_="index-imgcontent-title")
                if title_elem:
                    album_title = title_elem.get_text(strip=True)
        
        # 方法3: 简化的标题提取 - 使用alt属性或文件名作为标题
        if not album_title:
            # 查找图片标签
            img_tag = img_link.find("img")
            if img_tag:
                # 使用alt属性作为标题
                album_title = img_tag.get("alt", "")
                if not album_title:
                    # 使用图片文件名作为标题
                    img_src = img_tag.get("src", "")
                    if img_src:
                        album_title = os.path.basename(img_src).split('.')[0]
        
        # 如果还是没有标题，使用默认标题
        if not album_title:
            album_title = f"专辑_{i+1}"
        
        # 清洗标题
        album_title = sanitize_filename(album_title)
        
        # 添加到专辑列表
        albums.append((album_title, album_url))
        logging.info(f"提取到专辑: {album_title} -> {album_url}")
    
    logging.info(f"列表页解析完成，共提取到 {len(albums)} 个专辑")
    return albums

def parse_images_on_album_page(html: str, base_url: str) -> Set[str]:
    """从相册页HTML中解析出所有高清图片的绝对URL集合。"""
    soup = BeautifulSoup(html, "html.parser")
    image_urls = set()
    
    # 查找所有图片，优先选择高清大图
    for img in soup.find_all("img"):
        src = img.get("src", "")
        
        # 检查是否为有效的图片URL
        if src.startswith("/Files/images/") and (src.endswith(".webp") or src.endswith(".jpg") or src.endswith(".png")):
            # 高清图片格式: /Files/images/20260113/6390391274363781897617252.webp
            # 缩略图格式: /Files/images/202601/1c6ef29491e444428639530fb1ae1b85.webp
            # 只选择带完整日期（8位数字）的高清图
            import re
            # 检查路径中是否包含8位数字的日期
            if re.search(r'/\d{8}/', src):
                image_urls.add(urljoin(base_url, src))
    
    return image_urls

# -------- 下载核心逻辑 --------
def download_single_image(session: requests.Session, url: str, album_dir: str, verify: bool, retries: int, timeout: int, current_index: int, total_images: int) -> str:
    """下载单张图片，并根据内容验证有效性。"""
    filename = os.path.basename(urlparse(url).path)
    dest_path = os.path.join(album_dir, filename)
    
    progress_prefix = f"({current_index}/{total_images})"

    # 1. 检查已存在的文件
    if os.path.exists(dest_path):
        if is_image_valid_file(dest_path, verify):
            logging.info("%s 跳过 (已存在且有效): %s", progress_prefix, dest_path)
            return "skipped"
        else:
            # 文件存在，但验证失败 (损坏、太小或是HTML)
            logging.warning("%s 重新下载 (文件无效或损坏): %s", progress_prefix, dest_path)
            try:
                os.remove(dest_path) # 删除损坏的旧文件
            except OSError as e:
                logging.error("%s 无法删除旧的损坏文件: %s", progress_prefix, e)

    # 2. 执行下载 (文件不存在 或 文件损坏)
    data = request_with_retry(session, url, retries=retries, timeout=timeout, is_binary=True)
    
    if not data:
        logging.warning("%s 下载失败 (未获取到数据): %s", progress_prefix, url)
        return "fail"
    
    # 3. 验证新下载的数据 (在保存前)
    if not is_image_valid_bytes(data, verify):
        logging.warning("%s 下载的内容验证失败(损坏或HTML)，抛弃: %s", progress_prefix, url)
        return "fail"

    # 4. 保存 (数据已验证)
    if save_bytes_atomic(dest_path, data):
        logging.info("%s 下载成功: %s", progress_prefix, dest_path)
        return "ok"
    else:
        logging.warning("%s 下载后保存文件失败: %s", progress_prefix, dest_path)
        return "fail"

def parse_album_total_pages(html: str) -> int:
    """从专辑页HTML中解析出总页数。"""
    soup = BeautifulSoup(html, "html.parser")
    pagination = soup.find("ul", class_="pagination")
    
    if not pagination:
        return 1
    
    page_numbers = {1}
    for li in pagination.find_all("li"):
        a_tag = li.find("a")
        if a_tag:
            text = a_tag.get_text(strip=True)
            if text.isdigit():
                page_numbers.add(int(text))
    
    return max(page_numbers) if page_numbers else 1

def process_album(session: requests.Session, title: str, url: str, save_root: str, verify: bool, retries: int, timeout: int, album_index: int, total_albums: int) -> Dict[str, int]:
    """处理单个相册：进入专辑页 -> 获取专辑内总页数 -> 遍历专辑内所有分页 -> 提取图片链接 -> 并发下载图片。"""
    # 相册并发任务之间的延迟
    time.sleep(get_random_delay(DEFAULT_ALBUM_SLEEP_MIN, DEFAULT_ALBUM_SLEEP_MAX))
    
    # 打印专辑总进度
    log_prefix = f"[专辑 {album_index}/{total_albums}] {title}"
    logging.info("%s -> 正在请求专辑页: %s", log_prefix, url)

    # 获取专辑首页
    album_html = request_with_retry(session, url, retries=retries, timeout=timeout)
    if not album_html:
        logging.error("%s 无法获取专辑页。", log_prefix)
        return {"ok": 0, "skipped": 0, "fail": 1}
    
    # 解析专辑总页数
    total_pages = parse_album_total_pages(album_html)
    logging.info("%s -> 专辑共有 %d 页", log_prefix, total_pages)
    
    # 遍历专辑内所有分页，提取所有图片URL
    all_image_urls = set()
    
    for page_num in range(1, total_pages + 1):
        # 构建分页URL
        if page_num == 1:
            page_url = url
        else:
            page_url = f"{url}?page={page_num}"
        
        logging.info("%s -> 正在请求专辑分页 %d/%d: %s", log_prefix, page_num, total_pages, page_url)
        
        # 请求分页
        page_html = request_with_retry(session, page_url, retries=retries, timeout=timeout)
        if not page_html:
            logging.warning("%s -> 无法获取专辑分页 %d/%d", log_prefix, page_num, total_pages)
            continue
        
        # 提取当前分页的图片URL
        page_image_urls = parse_images_on_album_page(page_html, BASE_URL)
        all_image_urls.update(page_image_urls)
        
        logging.info("%s -> 分页 %d/%d 提取到 %d 张图片", log_prefix, page_num, total_pages, len(page_image_urls))
        
        # 分页之间添加小延迟
        time.sleep(get_random_delay(1.0, 2.0))
    
    if not all_image_urls:
        logging.warning("%s 未解析到任何图片。", log_prefix)
        return {"ok": 0, "skipped": 0, "fail": 0}

    # 打印总图片数
    total_images = len(all_image_urls)
    logging.info("%s -> 发现 %d 张图片，开始下载...", log_prefix, total_images)

    # 并发下载所有图片
    album_dir = os.path.join(save_root, title)
    os.makedirs(album_dir, exist_ok=True)
    
    results = {"ok": 0, "skipped": 0, "fail": 0}
    
    # 准备一个带序号的图片URL列表，便于传递进度
    indexed_image_urls = list(enumerate(all_image_urls, start=1))

    with ThreadPoolExecutor(max_workers=DEFAULT_CONCURRENCY_IMAGE, thread_name_prefix='ImageDownloader') as executor:
        future_map = {
            executor.submit(
                download_single_image, 
                session, 
                img_url, 
                album_dir, 
                verify, 
                retries, 
                timeout, 
                index, 
                total_images
            ): img_url 
            for index, img_url in indexed_image_urls
        }
        
        # 遍历完成的任务
        for future in as_completed(future_map):
            try:
                status = future.result()
                results[status] += 1
            except Exception as e:
                img_url = future_map[future]
                logging.exception("图片下载任务异常 [%s]", img_url)
                results["fail"] += 1

    # 打印结果
    logging.info("%s -> 处理完成。结果: 成功: %d, 跳过: %d, 失败: %d", 
                 log_prefix, results['ok'], results['skipped'], results['fail'])
    return results

# -------- 主函数 --------
def main():
    parser = argparse.ArgumentParser(description="凸凹吧相册爬虫", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-d", "--dir", help="图片保存的根目录")
    parser.add_argument("--verify", action="store_true", default=True, help="启用严格的图像验证")
    parser.add_argument("-r", "--retries", type=int, default=DEFAULT_RETRIES, help="请求失败最大重试次数")
    parser.add_argument("-t", "--timeout", type=int, default=DEFAULT_TIMEOUT, help="请求超时时间(秒)")
    parser.add_argument("-c", "--album-concurrency", type=int, default=DEFAULT_CONCURRENCY_ALBUM, help="并发处理的专辑数量")
    
    args = parser.parse_args()
    
    # 用户输入保存地址功能
    if args.dir:
        save_dir = args.dir
    else:
        save_dir = input("请输入保存地址 (留空则使用默认地址): ").strip()
        if not save_dir:
            save_dir = DEFAULT_SAVE_DIR
    
    save_dir = os.path.abspath(save_dir)
    
    # 创建保存目录
    os.makedirs(save_dir, exist_ok=True)
    
    # 配置参数
    verify = args.verify
    retries = args.retries
    timeout = args.timeout
    album_concurrency = args.album_concurrency
    
    logging.info("开始爬取凸凹吧 (https://www.tuao.cc/)")
    logging.info(f"图片将保存到: {save_dir}")
    
    # 创建会话
    session = make_session()
    
    summary = {"ok": 0, "skipped": 0, "fail": 0, "albums_processed": 0}
    all_albums_to_process = []
    seen_album_urls: Set[str] = set()
    
    # 1. 分析类型个数
    total_categories = len(CATEGORIES)
    logging.info(f"\n=== 分析类型信息 ===")
    logging.info(f"发现 {total_categories} 个类型：")
    for i, (category_name, _) in enumerate(CATEGORIES, 1):
        logging.info(f"  {i}. {category_name}")
    logging.info("==================")
    
    # 2. 遍历所有类型，收集所有相册链接
    logging.info("\n=== 开始收集所有类型的相册链接 ===")
    
    # 新增：记录上一页的专辑链接，用于去重
    previous_page_album_urls = set()
    
    for category_index, (category_name, category_path) in enumerate(CATEGORIES, 1):
        logging.info("\n" + "="*60)
        logging.info(f"[{category_index}/{total_categories}] 正在处理类型: {category_name}")
        logging.info("="*60)
        
        # 构建分类首页URL
        category_url = urljoin(BASE_URL, category_path)
        current_url = category_url
        page_count = 1
        
        # 遍历当前分类的所有分页
        while True:
            logging.info(f"[分类: {category_name}] 列表页 {page_count}: 正在请求 {current_url}")
            
            list_html = request_with_retry(session, current_url, retries=retries, timeout=timeout)
            if not list_html:
                logging.warning("获取列表页失败: %s", current_url)
                time.sleep(get_random_delay(DEFAULT_PAGE_SLEEP_MIN, DEFAULT_PAGE_SLEEP_MAX))
                continue
            
            # 解析当前页的相册
            soup = BeautifulSoup(list_html, "html.parser")
            current_page_albums = parse_albums_on_listing_page(list_html, BASE_URL)
            
            # 新增：提取当前页面的所有专辑URL
            current_page_album_urls = {url for _, url in current_page_albums}
            
            # 新增：检查当前页面与上一页的专辑是否完全相同
            if previous_page_album_urls and previous_page_album_urls == current_page_album_urls:
                logging.warning(f"[{category_name}] 列表页 {page_count} 与上一页的专辑完全相同，存在重复爬取情况")
                
                if category_index == total_categories:
                    # 新增：如果是最后一个类别，完成爬取
                    logging.info("[{category_index}/{total_categories}] 当前是最后一个类别，且页面重复，结束爬取")
                    break
                else:
                    # 新增：不是最后一个类别，进入下一个类别
                    logging.info("[{category_index}/{total_categories}] 不是最后一个类别，进入下一个类别")
                    break
            
            # 添加到待处理列表
            newly_added = 0
            for title, url in current_page_albums:
                if url not in seen_album_urls:
                    seen_album_urls.add(url)
                    all_albums_to_process.append((title, url))
                    newly_added += 1
            
            logging.info(f"[分类: {category_name}] 列表页 {page_count} 完成。发现 {newly_added} 个新专辑。")
            
            # 新增：更新上一页专辑URL集合
            previous_page_album_urls = current_page_album_urls
            
            # 检查下一页
            next_page_url = parse_next_page(soup)
            if next_page_url:
                current_url = urljoin(BASE_URL, next_page_url)
                page_count += 1
                logging.info(f"[{category_name}] 发现下一页，将继续爬取第 {page_count} 页")
                # 随机延迟4~8秒
                time.sleep(get_random_delay(DEFAULT_PAGE_SLEEP_MIN, DEFAULT_PAGE_SLEEP_MAX))
            else:
                logging.info(f"[{category_index}/{total_categories}] 类型 {category_name} 已完成所有分页爬取")
                # 新增：进入新类别前清空上一页记录
                previous_page_album_urls = set()
                break
    
    # 收集完成
    logging.info("\n" + "="*60)
    logging.info("=== 所有类型的相册链接收集完成 ===")
    
    # 处理所有收集到的相册
    total_albums = len(all_albums_to_process)
    if total_albums == 0:
        logging.warning("未找到任何专辑，程序结束。")
        return
        
    logging.info("\n" + "="*70)
    logging.info(f"共收集到 {total_albums} 个待处理专辑，开始并发下载...")
    logging.info("="*70)
    
    with ThreadPoolExecutor(max_workers=album_concurrency, thread_name_prefix='AlbumProcessor') as executor:
        future_map: Dict[Any, Tuple[str, str, int]] = {}
        
        for index, (title, url) in enumerate(all_albums_to_process, start=1):
            future = executor.submit(
                process_album, 
                session, title, url, save_dir, verify, 
                retries, timeout, index, total_albums
            )
            future_map[future] = (title, url, index)
        
        logging.info("已提交 %d 个相册任务，等待完成...", total_albums)
        
        for future in as_completed(future_map):
            album_title, album_url, index = future_map[future]
            
            try:
                result = future.result() 
                summary["ok"] += result["ok"]
                summary["skipped"] += result["skipped"]
                summary["fail"] += result["fail"]
                summary["albums_processed"] += 1
                
            except Exception as e:
                logging.error("[%d/%d] 专辑处理任务异常 [%s] %s: %s", index, total_albums, album_title, album_url, e)
                summary["albums_processed"] += 1
    
    # 打印最终结果
    logging.info("\n" + "="*70)
    logging.info("程序执行完毕。爬取任务总结：")
    logging.info(f"  处理的专辑总数: {summary['albums_processed']} / {total_albums}")
    logging.info("-" * 25)
    logging.info(f"  [成功下载]: {summary['ok']} 张")
    logging.info(f"  [跳过 (已存在)]: {summary['skipped']} 张")
    logging.info(f"  [失败总数]: {summary['fail']} 张")
    logging.info("="*70)
    logging.info(f"所有图片已保存到: {save_dir}")

if __name__ == "__main__":
    main()
