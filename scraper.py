"""
SOL.org.tr Article Scraper - Individual File Storage
Scrapes articles and saves each as a separate text file

Installation:
    pip install playwright beautifulsoup4
    playwright install chromium

Usage:
    python scraper.py
"""

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from datetime import datetime
import json
import os
import re
import sys

def sanitize_filename(text):
    """Create a safe filename from text"""
    # Remove special characters
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    # Replace spaces with underscores
    text = text.replace(' ', '_')
    # Limit length
    return text[:100]

def clean_text(text):
    """Clean and normalize text"""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_article_content(page, url):
    """Extract full article content"""
    try:
        print(f"      📖 Loading: {url}")
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(3000)
        
        html_content = page.content()
        soup = BeautifulSoup(html_content, 'html.parser')
        
        article_data = {
            'url': url,
            'title': '',
            'author': '',
            'date': '',
            'full_text': '',
            'paragraphs': []
        }
        
        # Extract title
        for selector in ['h1', 'h1.title', '.page-title', 'article h1']:
            element = soup.select_one(selector)
            if element:
                title = clean_text(element.get_text())
                if title and len(title) > 10:
                    article_data['title'] = title
                    break
        
        # Extract author
        for selector in ['.field--name-field-author', '.author', '.submitted-by', '[rel="author"]']:
            element = soup.select_one(selector)
            if element:
                author = clean_text(element.get_text())
                author = re.sub(r'(Yazar:|By:|yazarı:)', '', author, flags=re.IGNORECASE).strip()
                if author and len(author) < 100:
                    article_data['author'] = author
                    break
        
        # Extract date
        for selector in ['time', '.date', '.submitted', '.field--name-post-date']:
            element = soup.select_one(selector)
            if element:
                date = element.get('datetime', '') or clean_text(element.get_text())
                if date:
                    article_data['date'] = date
                    break
        
        # Extract content
        paragraphs = []
        content_selectors = [
            'article .field--name-body',
            '.field--name-body',
            'article .field--name-field-body',
            '.field--name-field-body',
            '.node__content .field--type-text-with-summary',
            'article .content',
            '.article-content',
            '.node__content'
        ]
        
        for selector in content_selectors:
            content_div = soup.select_one(selector)
            if content_div:
                for p in content_div.find_all('p'):
                    text = clean_text(p.get_text())
                    if text and len(text) > 30:
                        paragraphs.append(text)
                if paragraphs:
                    break
        
        # Fallback methods
        if not paragraphs:
            article_tag = soup.find('article')
            if article_tag:
                for p in article_tag.find_all('p'):
                    text = clean_text(p.get_text())
                    if text and len(text) > 30:
                        paragraphs.append(text)
        
        if not paragraphs:
            main = soup.find('main') or soup.find('div', {'role': 'main'})
            if main:
                for p in main.find_all('p'):
                    text = clean_text(p.get_text())
                    if text and len(text) > 30:
                        paragraphs.append(text)
        
        article_data['paragraphs'] = paragraphs
        article_data['full_text'] = '\n\n'.join(paragraphs)
        article_data['word_count'] = len(article_data['full_text'].split())
        
        if paragraphs:
            print(f"      ✅ {len(paragraphs)} paragraphs, {article_data['word_count']} words")
            return article_data
        else:
            print(f"      ❌ No content found")
            return None
        
    except Exception as e:
        print(f"      ❌ Error: {e}")
        return None

def scrape_articles(num_articles=4, output_dir='articles'):
    """
    Scrape articles and save each to separate files
    """
    url = "https://haber.sol.org.tr/"
    
    # Create output directories
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, 'txt'), exist_ok=True)
    os.makedirs(os.path.join(output_dir, 'metadata'), exist_ok=True)
    
    print("=" * 80)
    print("SOL.org.tr Article Scraper")
    print("=" * 80)
    print(f"\n🎯 Target: {num_articles} articles")
    print(f"📁 Output directory: {output_dir}/")
    print("-" * 80)
    
    articles_info = []
    
    try:
        with sync_playwright() as p:
            print("\n🌐 Launching browser...")
            browser = p.chromium.launch(headless=True)
            
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                locale='tr-TR'
            )
            
            page = context.new_page()
            page.set_default_timeout(30000)
            
            print(f"📡 Loading homepage: {url}")
            page.goto(url, wait_until='domcontentloaded')
            page.wait_for_timeout(3000)
            
            # Find article links
            print("\n🔍 Finding article links...")
            article_links = []
            all_links = page.query_selector_all('a[href]')
            
            seen_urls = set()
            for link in all_links:
                if len(article_links) >= num_articles * 3:
                    break
                
                href = link.get_attribute('href')
                if not href or href in seen_urls:
                    continue
                
                if href.startswith('/haber/') or href.startswith('/yazarlar/'):
                    seen_urls.add(href)
                    if href.startswith('/'):
                        full_url = f"https://haber.sol.org.tr{href}"
                    else:
                        full_url = href
                    article_links.append(full_url)
            
            print(f"   ✅ Found {len(article_links)} potential articles")
            
            # Extract and save articles
            print(f"\n📰 Extracting articles...")
            print("-" * 80)
            
            article_counter = 0
            for article_url in article_links:
                if article_counter >= num_articles:
                    break
                
                print(f"\n   Article {article_counter + 1}/{num_articles}:")
                article_data = extract_article_content(page, article_url)
                
                if article_data and article_data.get('full_text'):
                    article_counter += 1
                    
                    # Generate filename
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    safe_title = sanitize_filename(article_data['title'])
                    base_filename = f"{timestamp}_{safe_title}"
                    
                    # Save text file
                    txt_path = os.path.join(output_dir, 'txt', f"{base_filename}.txt")
                    with open(txt_path, 'w', encoding='utf-8') as f:
                        f.write(f"{article_data['title']}\n")
                        f.write("=" * 80 + "\n\n")
                        if article_data.get('author'):
                            f.write(f"Yazar: {article_data['author']}\n")
                        if article_data.get('date'):
                            f.write(f"Tarih: {article_data['date']}\n")
                        f.write(f"Kaynak: {article_data['url']}\n")
                        f.write("\n" + "=" * 80 + "\n\n")
                        f.write(article_data['full_text'])
                    
                    # Save metadata
                    metadata_path = os.path.join(output_dir, 'metadata', f"{base_filename}.json")
                    metadata = {
                        'id': article_counter,
                        'filename': base_filename,
                        'title': article_data['title'],
                        'author': article_data.get('author', 'Unknown'),
                        'date': article_data.get('date', ''),
                        'url': article_data['url'],
                        'word_count': article_data['word_count'],
                        'txt_file': f"txt/{base_filename}.txt",
                        'audio_file': f"audio/{base_filename}.mp3",
                        'scraped_at': datetime.now().isoformat()
                    }
                    
                    with open(metadata_path, 'w', encoding='utf-8') as f:
                        json.dump(metadata, f, ensure_ascii=False, indent=2)
                    
                    articles_info.append(metadata)
                    
                    print(f"      💾 Saved: {base_filename}.txt")
            
            browser.close()
            
            # Save index file
            index_path = os.path.join(output_dir, 'index.json')
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(articles_info, f, ensure_ascii=False, indent=2)
            
            print(f"\n{'=' * 80}")
            print(f"✅ Successfully scraped {len(articles_info)} articles")
            print(f"📁 Files saved in: {output_dir}/")
            print(f"   - Text files: {output_dir}/txt/")
            print(f"   - Metadata: {output_dir}/metadata/")
            print(f"   - Index: {output_dir}/index.json")
            print("=" * 80)
            
            return articles_info
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return []

def main():
    # You can change the number of articles here
    articles = scrape_articles(num_articles=4, output_dir='articles')
    
    if articles:
        print(f"\n✅ Next step: Run TTS converter")
        print(f"   python tts_converter.py")
        return 0
    else:
        print("\n❌ No articles scraped!")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
