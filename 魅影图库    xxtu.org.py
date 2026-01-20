import requests
from bs4 import BeautifulSoup
import concurrent.futures
from PIL import Image
import io
import os
import time
import random
import argparse
import logging
import shutil
import re
import sys
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 设置日志格式
log_file = "crawler.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# 禁用所有第三方库的日志
for name in logging.root.manager.loggerDict:
    if name not in ['__main__', 'crawler']:
        logging.getLogger(name).setLevel(logging.CRITICAL)

class GalleryCrawler:
    def __init__(self, save_path, verify=False):
        self.save_path = save_path
        self.verify = verify
        self.session = self._create_session()
        
        # 初始化列表
        self.waiting_list = []
        self.downloading_list = []
        self.completed_list = []
        self.failed_list = []
        
        # 创建保存目录
        if not os.path.exists(self.save_path):
            os.makedirs(self.save_path)
    
    def _create_session(self):
        """创建带连接池和重试机制的session"""
        session = requests.Session()
        retry = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        # 设置headers模拟浏览器
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        return session
    
    def _sanitize_filename(self, filename):
        """清理文件名，将特殊字符替换为空格"""
        return re.sub(r'[\\/:*?"<>|]', ' ', filename)
    
    def get_all_albums(self):
        """获取所有相册链接和名称"""
        base_url = "https://xxtu.org/"
        albums = []
        page = 1
        max_pages = 100  # 设置较大的最大页数限制，确保获取所有相册
        
        logger.info("开始获取所有相册...")
        print("🚀 开始获取所有相册...")
        
        while page <= max_pages:
            try:
                # 构建分页URL
                if page == 1:
                    current_url = base_url
                else:
                    current_url = f"{base_url}?paged={page}"
                
                logger.info(f"正在获取第 {page} 页相册，URL: {current_url}")
                print(f"📄 正在获取第 {page} 页相册，URL: {current_url}")
                # 随机延迟4-8秒
                delay = random.uniform(4, 8)
                print(f"⏱️  随机延迟 {delay:.1f} 秒...")
                time.sleep(delay)
                
                # 获取页面内容
                start_time = time.time()
                response = self.session.get(current_url, timeout=30)
                response.raise_for_status()
                end_time = time.time()
                
                # 计算下载速度
                content_length = len(response.content)
                elapsed_time = end_time - start_time
                if elapsed_time > 0:
                    speed = content_length / elapsed_time / 1024  # KB/s
                    print(f"📥 页面下载完成，大小: {content_length/1024:.1f} KB，耗时: {elapsed_time:.2f} 秒，速度: {speed:.1f} KB/s")
                
                # 解析页面
                print(f"🔍 正在解析页面...")
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # 查找所有相册项 - 优化选择器，确保能找到所有相册项
                album_items = soup.find_all('article')
                if not album_items:
                    # 尝试另一种可能的选择器
                    print(f"🔍 未找到article标签，尝试使用div.post选择器...")
                    album_items = soup.find_all('div', class_='post')
                
                if not album_items:
                    logger.info(f"第 {page} 页未找到相册项，已获取全部相册")
                    print(f"📄 第 {page} 页未找到相册项，已获取全部相册")
                    break
                
                print(f"✅ 找到 {len(album_items)} 个相册项")
                
                # 提取相册信息
                new_albums = 0
                print(f"📸 正在提取相册信息...")
                for item in album_items:
                    # 查找相册链接
                    a_tag = item.find('a')
                    if a_tag and 'href' in a_tag.attrs:
                        album_url = a_tag['href']
                        # 查找相册名称
                        title_tag = item.find('h2', class_='entry-title')
                        if not title_tag:
                            # 尝试其他可能的标题标签
                            title_tag = item.find('h1', class_='entry-title')
                            if not title_tag:
                                title_tag = item.find('h3', class_='entry-title')
                        
                        if title_tag:
                            album_name = title_tag.text.strip()
                            # 清理相册名称，用于文件夹命名
                            sanitized_name = self._sanitize_filename(album_name)
                            
                            # 检查是否已存在该相册
                            album_exists = any(existing_album[2] == album_url for existing_album in albums)
                            if not album_exists:
                                albums.append((sanitized_name, album_name, album_url))
                                new_albums += 1
                                print(f"🎉 检索到相册: {album_name}")
                
                logger.info(f"第 {page} 页新增 {new_albums} 个相册，累计 {len(albums)} 个相册")
                print(f"📊 第 {page} 页处理完成，新增 {new_albums} 个相册，累计 {len(albums)} 个相册")
                
                # 检查是否获取到新相册
                if new_albums == 0:
                    logger.info(f"第 {page} 页未新增任何相册，检查是否为最后一页")
                    print(f"📄 第 {page} 页未新增任何相册，检查是否为最后一页")
                    # 如果连续2页没有新增相册，或者页码超过5页，则停止
                    if page > 5:  # 确保至少获取5页
                        print(f"🎉 已获取到 {len(albums)} 个相册，结束相册获取")
                        break
                
                # 继续获取下一页
                page += 1
                    
            except requests.exceptions.HTTPError as e:
                # 处理HTTP错误
                if e.response.status_code == 404:
                    # 404错误，说明页面不存在，是最后一页
                    logger.info(f"第 {page} 页返回404错误，已到达最后一页")
                    print(f"✅ 第 {page} 页返回404错误，已到达最后一页")
                    break
                else:
                    # 其他HTTP错误，重试
                    logger.error(f"HTTP请求失败: {e}")
                    print(f"❌ HTTP请求失败: {e}")
                    logger.info("按任意键重试，或按Ctrl+C退出...")
                    input()
                    continue
            except requests.exceptions.RequestException as e:
                # 其他网络请求错误，重试
                logger.error(f"网络请求失败: {e}")
                print(f"❌ 网络请求失败: {e}")
                logger.info("按任意键重试，或按Ctrl+C退出...")
                input()
                continue
            except Exception as e:
                logger.error(f"获取第 {page} 页相册失败: {e}")
                print(f"❌ 获取第 {page} 页相册失败: {e}")
                # 打印完整的错误堆栈，便于调试
                import traceback
                traceback.print_exc()
                break
        
        logger.info(f"共找到 {len(albums)} 个相册")
        print(f"🎉 相册检索完成，共找到 {len(albums)} 个相册")
        return albums
    
    def validate_image(self, image_path):
        """验证图片是否损坏"""
        # 检查文件大小
        if os.path.getsize(image_path) == 0:
            logger.error(f"图片 {image_path} 大小为0")
            return False
        
        # 验证图片完整性
        try:
            with Image.open(image_path) as img:
                img.verify()
            return True
        except Exception as e:
            logger.error(f"图片 {image_path} 损坏: {e}")
            return False
    
    def download_image(self, image_url, save_path):
        """下载单张图片"""
        retry_count = 0
        max_retries = 5
        
        img_name = os.path.basename(save_path)
        print(f"📥 开始下载图片: {img_name}")
        
        while retry_count < max_retries:
            try:
                # 随机延迟4-8秒
                delay = random.uniform(4, 8)
                print(f"⏱️  随机延迟 {delay:.1f} 秒...")
                time.sleep(delay)
                
                # 发送请求
                print(f"🔗 正在连接: {image_url}")
                response = self.session.get(image_url, timeout=60, stream=True)
                response.raise_for_status()
                
                # 验证响应状态码
                if response.status_code != 200:
                    error_msg = f"❌ 图片链接返回状态码: {response.status_code}"
                    logger.error(f"图片链接 {image_url} 返回状态码: {response.status_code}")
                    print(error_msg)
                    retry_count += 1
                    continue
                
                # 验证响应内容是否为图片
                content_type = response.headers.get('Content-Type', '')
                
                if not content_type.startswith('image/'):
                    error_msg = f"❌ 返回非图片内容: {content_type}"
                    logger.error(f"图片链接 {image_url} 返回非图片内容: {content_type}")
                    print(error_msg)
                    retry_count += 1
                    continue
                
                # 再次检查文件是否已存在（避免并发下载同一文件）
                if os.path.exists(save_path):
                    if self.validate_image(save_path):
                        info_msg = f"✅ 图片已存在且完整，跳过下载"
                        logger.info(f"图片 {image_url} 已存在且完整，跳过下载")
                        print(info_msg)
                        return True
                    else:
                        info_msg = f"🔄 图片已存在但损坏，重新下载"
                        logger.info(f"图片 {image_url} 已存在但损坏，重新下载")
                        print(info_msg)
                
                # 获取文件大小
                content_length = response.headers.get('Content-Length')
                total_size = int(content_length) if content_length else 0
                if total_size > 0:
                    print(f"📊 文件大小: {total_size/1024:.1f} KB")
                
                # 原子化写入文件
                temp_path = save_path + '.tmp'
                
                # 下载并保存文件
                print(f"💾 正在保存到: {save_path}")
                start_time = time.time()
                downloaded_size = 0
                with open(temp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            
                            # 计算并显示下载进度和速度
                            elapsed_time = time.time() - start_time
                            if elapsed_time > 0.5:  # 每0.5秒更新一次进度
                                speed = downloaded_size / elapsed_time / 1024  # KB/s
                                if total_size > 0:
                                    progress = downloaded_size / total_size * 100
                                    print(f"📊 下载进度: {progress:.1f}% ({downloaded_size/1024:.1f} KB/{total_size/1024:.1f} KB)，速度: {speed:.1f} KB/s", end='\r')
                                else:
                                    print(f"📊 下载进度: {downloaded_size/1024:.1f} KB，速度: {speed:.1f} KB/s", end='\r')
                
                # 下载完成，计算总速度
                end_time = time.time()
                elapsed_time = end_time - start_time
                total_downloaded = downloaded_size
                if elapsed_time > 0:
                    speed = total_downloaded / elapsed_time / 1024  # KB/s
                    info_msg = f"📊 下载完成，耗时: {elapsed_time:.2f} 秒，速度: {speed:.1f} KB/s"
                    print(f"\n{info_msg}")
                    logger.info(f"[{img_name}] 下载完成，文件大小: {total_downloaded} 字节，耗时: {elapsed_time:.2f} 秒，速度: {speed:.1f} KB/s")
                
                # 检查文件大小是否为0
                if total_downloaded == 0:
                    error_msg = f"❌ 下载后文件大小为0"
                    logger.error(f"图片 {image_url} 下载后文件大小为0")
                    print(f"\n{error_msg}")
                    os.remove(temp_path)
                    retry_count += 1
                    continue
                
                # 简单验证文件开头的魔法数字
                print(f"🔍 正在验证图片完整性...")
                with open(temp_path, 'rb') as f:
                    magic_number = f.read(8)
                
                # 常见图片格式的魔法数字
                valid_magic_numbers = {
                    b'\xFF\xD8\xFF': ['jpg', 'jpeg'],
                    b'\x89\x50\x4E\x47': ['png'],
                    b'\x47\x49\x46\x38': ['gif'],
                    b'\x42\x4D': ['bmp']
                }
                
                is_valid = False
                for magic, formats in valid_magic_numbers.items():
                    if magic_number.startswith(magic):
                        is_valid = True
                        break
                
                if not is_valid:
                    error_msg = f"❌ 图片魔法数字无效: {magic_number}"
                    logger.error(f"图片 {image_url} 魔法数字无效: {magic_number}")
                    print(error_msg)
                    os.remove(temp_path)
                    retry_count += 1
                    continue
                
                # 验证图片完整性
                if self.validate_image(temp_path):
                    try:
                        os.rename(temp_path, save_path)
                        success_msg = f"✅ 图片下载完成: {img_name}"
                        print(success_msg)
                        return True
                    except FileExistsError:
                        # 如果文件在下载过程中被其他线程创建，再次验证
                        os.remove(temp_path)
                        if os.path.exists(save_path):
                            if self.validate_image(save_path):
                                info_msg = f"✅ 图片已被其他线程下载完成，跳过"
                                logger.info(f"图片 {image_url} 已被其他线程下载完成，跳过")
                                print(info_msg)
                                return True
                            else:
                                error_msg = f"❌ 图片已存在但损坏，需要重新下载"
                                logger.error(f"图片 {image_url} 已存在但损坏，需要重新下载")
                                print(error_msg)
                                retry_count += 1
                                continue
                else:
                    error_msg = f"❌ 图片下载后损坏，正在重试... ({retry_count+1}/{max_retries})"
                    logger.error(f"图片 {image_url} 下载后损坏，正在重试... ({retry_count+1}/{max_retries})")
                    print(error_msg)
                    os.remove(temp_path)
                    retry_count += 1
            except requests.exceptions.RequestException as e:
                retry_count += 1
                error_msg = f"❌ 网络请求失败: {e}，正在重试... ({retry_count}/{max_retries})"
                logger.error(f"网络请求失败，下载图片 {image_url} 失败: {e}，正在重试... ({retry_count}/{max_retries})")
                print(error_msg)
                time.sleep(random.uniform(4, 8))
            except Exception as e:
                retry_count += 1
                error_msg = f"❌ 下载失败: {e}，正在重试... ({retry_count}/{max_retries})"
                logger.error(f"下载图片 {image_url} 失败: {e}，正在重试... ({retry_count}/{max_retries})")
                print(error_msg)
                # 打印完整的错误堆栈，以便调试
                import traceback
                traceback.print_exc()
                time.sleep(random.uniform(4, 8))
        
        error_msg = f"❌ 图片下载失败: {img_name}"
        print(error_msg)
        return False
    
    def download_album(self, album_info):
        """下载单个相册"""
        sanitized_name, original_name, album_url = album_info
        
        # 打印相册开始信息
        print(f"\n🎊 开始下载相册: {original_name}")
        print(f"📚 相册链接: {album_url}")
        logger.info(f"开始下载相册: {original_name}")
        
        # 将相册添加到正在下载列表
        self.downloading_list.append(original_name)
        
        # 创建相册目录
        album_dir = os.path.join(self.save_path, sanitized_name)
        if not os.path.exists(album_dir):
            print(f"📁 创建相册目录: {album_dir}")
            os.makedirs(album_dir)
        
        try:
            # 随机延迟4-8秒
            delay = random.uniform(4, 8)
            print(f"⏱️  随机延迟 {delay:.1f} 秒...")
            time.sleep(delay)
            
            # 获取相册页面内容
            print(f"🔗 获取相册页面内容...")
            response = self.session.get(album_url, timeout=30)
            response.raise_for_status()
            
            # 解析相册页面，获取所有图片链接
            print(f"🔍 解析相册页面，提取图片链接...")
            soup = BeautifulSoup(response.text, 'html.parser')
            image_tags = soup.find_all('img')
            image_urls = []
            
            for img in image_tags:
                if 'src' in img.attrs:
                    img_url = img['src']
                    # 过滤掉不需要的图片（只保留jpg, jpeg, png, gif）
                    if img_url.endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        image_urls.append(img_url)
            
            total_images = len(image_urls)
            print(f"📸 相册包含 {total_images} 张图片")
            logger.info(f"相册 {original_name} 包含 {total_images} 张图片")
            
            # 并发下载相册中的图片 - 限制图片级并发为3-5个
            success_count = 0
            skip_count = 0
            fail_count = 0
            
            # 限制图片级并发数，避免并发过高
            img_max_workers = random.randint(3, 5)
            print(f"🚀 开始下载相册中的图片，使用 {img_max_workers} 个图片并发线程...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=img_max_workers) as executor:
                futures = {}
                for i, img_url in enumerate(image_urls):
                    # 使用原文件名保存图片
                    img_name = os.path.basename(img_url.split('?')[0])
                    img_path = os.path.join(album_dir, img_name)
                    
                    # 如果图片已存在且验证通过，则跳过
                    if os.path.exists(img_path):
                        if self.validate_image(img_path):
                            skip_msg = f"✅ 图片 {img_name} 已存在且完整，跳过下载"
                            print(skip_msg)
                            logger.info(f"[{original_name}] 图片 {img_name} 已存在且完整，跳过下载")
                            skip_count += 1
                            success_count += 1
                            continue
                        else:
                            print(f"🔄 图片 {img_name} 已存在但损坏，重新下载")
                            logger.info(f"[{original_name}] 图片 {img_name} 已存在但损坏，重新下载")
                    
                    future = executor.submit(self.download_image, img_url, img_path)
                    futures[future] = (img_url, img_name)
                
                # 等待所有图片下载完成
                total_futures = len(futures)
                completed_futures = 0
                
                for future in concurrent.futures.as_completed(futures):
                    completed_futures += 1
                    img_url, img_name = futures[future]
                    try:
                        result = future.result()
                        if result:
                            success_count += 1
                            print(f"📊 相册进度: {completed_futures}/{total_futures} 张，成功: {success_count}, 失败: {fail_count}, 跳过: {skip_count}")
                        else:
                            fail_count += 1
                            print(f"📊 相册进度: {completed_futures}/{total_futures} 张，成功: {success_count}, 失败: {fail_count}, 跳过: {skip_count}")
                    except Exception as e:
                        fail_count += 1
                        print(f"📊 相册进度: {completed_futures}/{total_futures} 张，成功: {success_count}, 失败: {fail_count}, 跳过: {skip_count}")
                        logger.error(f"[{original_name}] 处理图片 {img_name} 时出错: {e}")
            
            # 下载完成总结
            summary_msg = f"🎉 相册下载完成: {original_name}"
            print(f"\n{summary_msg}")
            print(f"📊 相册统计: 总图片数: {total_images}, 成功: {success_count}, 失败: {fail_count}, 跳过: {skip_count}")
            print(f"📁 保存目录: {album_dir}")
            
            logger.info(f"相册 {original_name} 下载完成，成功 {success_count}/{total_images} 张图片")
            
            # 从正在下载列表移除，添加到已完成列表
            self.downloading_list.remove(original_name)
            self.completed_list.append(original_name)
            
            return True
        except requests.exceptions.RequestException as e:
            error_msg = f"❌ 网络请求失败，处理相册 {original_name} 时出错: {e}"
            print(f"\n{error_msg}")
            logger.error(error_msg)
            # 从正在下载列表移除，添加到失败列表
            if original_name in self.downloading_list:
                self.downloading_list.remove(original_name)
            self.failed_list.append(original_name)
            return False
        except Exception as e:
            error_msg = f"❌ 处理相册 {original_name} 时出错: {e}"
            print(f"\n{error_msg}")
            logger.error(error_msg)
            # 从正在下载列表移除，添加到失败列表
            if original_name in self.downloading_list:
                self.downloading_list.remove(original_name)
            self.failed_list.append(original_name)
            return False
    
    def run(self):
        """主运行函数"""
        start_time = time.time()
        print("🚀 开始运行爬虫...")
        print(f"📅 开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 获取所有相册
        print("\n🎯 阶段1: 获取所有相册")
        albums = self.get_all_albums()
        total_albums = len(albums)
        print(f"🎉 共获取到 {total_albums} 个相册")
        
        # 将所有相册添加到待下载列表
        self.waiting_list = [album[1] for album in albums]
        print(f"📋 待下载列表已更新，共 {len(self.waiting_list)} 个相册")
        
        # 并发下载相册（3-5个并发）
        max_workers = random.randint(3, 5)
        print(f"\n🎯 阶段2: 开始下载相册")
        print(f"⚡ 使用 {max_workers} 个并发线程")
        print(f"📝 下载策略: 每个相册随机延迟4-8秒，每个图片随机延迟4-8秒")
        
        # 简单下载，不使用复杂的进度监控
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            print(f"\n📊 全局进度监控:")
            print(f"开始下载...")
            
            # 执行下载
            futures = {}
            for i, album in enumerate(albums):
                future = executor.submit(self.download_album, album)
                futures[future] = album[1]
            
            # 实时监控进度
            completed_albums = 0
            for future in concurrent.futures.as_completed(futures):
                completed_albums += 1
                album_name = futures[future]
                
                # 计算当前进度
                progress = completed_albums / total_albums * 100
                elapsed_time = time.time() - start_time
                albums_per_minute = completed_albums / (elapsed_time / 60) if elapsed_time > 0 else 0
                
                print(f"📊 全局进度: {progress:.1f}% ({completed_albums}/{total_albums} 个相册)，耗时: {elapsed_time:.2f} 秒，速度: {albums_per_minute:.1f} 个/分钟")
                print(f"📋 当前状态: 待下载: {len(self.waiting_list)}, 正在下载: {len(self.downloading_list)}, 已完成: {len(self.completed_list)}, 失败: {len(self.failed_list)}")
        
        # 处理失败列表
        if self.failed_list:
            print(f"\n⚠️  下载完成！发现失败项")
            print(f"📋 失败列表: {self.failed_list}")
            print(f"📊 初步统计: 总相册数: {total_albums}, 成功: {len(self.completed_list)}, 失败: {len(self.failed_list)}")
            
            # 询问用户是否重试失败的相册
            while True:
                user_input = input("\n🔄 是否重试失败的相册？(y/n): ").strip().lower()
                if user_input in ['y', 'n']:
                    break
                print("请输入 y 或 n")
            
            if user_input == 'y':
                print("\n🔄 开始重试失败的相册...")
                
                # 准备重试的相册列表
                retry_albums = []
                for album in albums:
                    if album[1] in self.failed_list:
                        retry_albums.append(album)
                
                print(f"📋 准备重试 {len(retry_albums)} 个失败的相册")
                
                # 重置失败列表
                self.failed_list = []
                
                # 重试下载
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {}
                    for album in retry_albums:
                        future = executor.submit(self.download_album, album)
                        futures[future] = album[1]
                    
                    # 实时监控重试进度
                    completed_retry = 0
                    total_retry = len(retry_albums)
                    for future in concurrent.futures.as_completed(futures):
                        completed_retry += 1
                        album_name = futures[future]
                        progress = completed_retry / total_retry * 100
                        print(f"📊 重试进度: {progress:.1f}% ({completed_retry}/{total_retry} 个相册)")
        
        # 计算总耗时
        total_time = time.time() - start_time
        
        # 打印最终结果
        print(f"\n🏆 最终下载结果！")
        print(f"📅 结束时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⏱️  总耗时: {total_time:.2f} 秒 ({total_time/60:.2f} 分钟)")
        print(f"📊 全局统计:")
        print(f"   总相册数: {total_albums}")
        print(f"   成功下载: {len(self.completed_list)}")
        print(f"   下载失败: {len(self.failed_list)}")
        print(f"   成功率: {len(self.completed_list)/total_albums*100:.1f}%")
        
        # 显示各个列表
        print(f"\n📋 列表详情:")
        print(f"   待下载列表: {self.waiting_list}")
        print(f"   正在下载列表: {self.downloading_list}")
        print(f"   已完成列表: {self.completed_list}")
        print(f"   失败列表: {self.failed_list}")
        
        print(f"\n🎉 爬虫运行完成！")
    
    def verify_existing_files(self):
        """验证并修复已存在的损坏文件"""
        logger.info("开始验证已存在的文件...")
        
        # 遍历所有相册目录
        for album_name in os.listdir(self.save_path):
            album_dir = os.path.join(self.save_path, album_name)
            if not os.path.isdir(album_dir):
                continue
            
            logger.info(f"验证相册: {album_name}")
            
            # 遍历相册中的所有图片
            for img_name in os.listdir(album_dir):
                img_path = os.path.join(album_dir, img_name)
                if not os.path.isfile(img_path):
                    continue
                
                # 如果图片损坏，则删除
                if not self.validate_image(img_path):
                    logger.info(f"删除损坏的图片: {img_path}")
                    os.remove(img_path)
        
        logger.info("文件验证完成！")

def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="魅影图库爬虫")
    parser.add_argument('--save-path', type=str, default="E:achong果影图库    xxtu.org", help="图片保存路径")
    parser.add_argument('--verify', action='store_true', help="验证并修复已存在的损坏文件")
    args = parser.parse_args()
    
    # 询问用户保存地址，若留空则使用默认
    default_path = args.save_path
    print(f"默认保存路径: {default_path}")
    user_input = input("请输入自定义保存路径（留空使用默认）: ").strip()
    
    if user_input:
        save_path = user_input
        print(f"使用自定义保存路径: {save_path}")
    else:
        save_path = default_path
        print(f"使用默认保存路径: {save_path}")
    
    # 初始化爬虫
    crawler = GalleryCrawler(save_path, args.verify)
    
    # 如果启用了验证模式，则先验证已存在的文件
    if args.verify:
        crawler.verify_existing_files()
    
    # 开始爬取
    crawler.run()

if __name__ == "__main__":
    main()