"""
Full text extractor for haber.sol.org.tr articles
Extracts complete article content for TTS or text analysis

Installation:
    pip install playwright beautifulsoup4
    playwright install chromium

Usage:
    python scrape_sol_fulltext.py
"""

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup
from datetime import datetime
import json
import sys
import re

def clean_text(text):
    """Clean and normalize text"""
    if not text:
        return ""
    
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove leading/trailing whitespace
    text = text.strip()
    return text

def extract_article_fulltext(page, url):
    """
    Extract full article text from a news article page
    """
    try:
        print(f"      📖 Loading: {url}")
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(3000)  # Wait for content
        
        # Get the full HTML
        html_content = page.content()
        soup = BeautifulSoup(html_content, 'html.parser')
        
        article_data = {
            'url': url,
            'title': '',
            'author': '',
            'date': '',
            'full_text': '',
            'paragraphs': [],
            'fetched_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Extract title - try multiple selectors
        title = None
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
        
        # Extract main content - try multiple approaches
        content_found = False
        paragraphs = []
        
        # Method 1: Look for Drupal/common CMS content fields
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
                # Get all paragraphs from this container
                for p in content_div.find_all('p'):
                    text = clean_text(p.get_text())
                    if text and len(text) > 30:  # Filter very short paragraphs
                        paragraphs.append(text)
                
                if paragraphs:
                    content_found = True
                    print(f"      ✅ Found content using: {selector}")
                    break
        
        # Method 2: If no content found, try article tag
        if not content_found:
            article_tag = soup.find('article')
            if article_tag:
                for p in article_tag.find_all('p'):
                    text = clean_text(p.get_text())
                    if text and len(text) > 30:
                        paragraphs.append(text)
                if paragraphs:
                    content_found = True
                    print(f"      ✅ Found content in <article> tag")
        
        # Method 3: Get all paragraphs from main content area
        if not content_found:
            main = soup.find('main') or soup.find('div', {'role': 'main'})
            if main:
                for p in main.find_all('p'):
                    text = clean_text(p.get_text())
                    if text and len(text) > 30:
                        paragraphs.append(text)
                if paragraphs:
                    content_found = True
                    print(f"      ✅ Found content in <main> tag")
        
        # Method 4: Fallback - get all paragraphs that look like article content
        if not content_found:
            print(f"      ⚠️  Using fallback method...")
            all_paragraphs = soup.find_all('p')
            for p in all_paragraphs:
                text = clean_text(p.get_text())
                # Filter: must be substantial text, not navigation/menu items
                if (text and 
                    len(text) > 50 and 
                    not any(skip in text.lower() for skip in ['menü', 'copyright', '©', 'cookie', 'giriş yap'])):
                    paragraphs.append(text)
            
            if paragraphs:
                content_found = True
                print(f"      ✅ Found {len(paragraphs)} paragraphs using fallback")
        
        # Store results
        article_data['paragraphs'] = paragraphs
        article_data['full_text'] = '\n\n'.join(paragraphs)
        article_data['word_count'] = len(article_data['full_text'].split())
        article_data['char_count'] = len(article_data['full_text'])
        
        if content_found:
            print(f"      📊 Extracted: {len(paragraphs)} paragraphs, {article_data['word_count']} words")
            return article_data
        else:
            print(f"      ❌ No content found")
            return None
        
    except Exception as e:
        print(f"      ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None


def fetch_articles_fulltext(num_articles=4, headless=True):
    """
    Fetch articles with full text content
    """
    url = "https://haber.sol.org.tr/"
    articles = []
    
    print("=" * 80)
    print("SOL.org.tr Full Text Scraper")
    print("=" * 80)
    print(f"\n🚀 Target: {num_articles} articles")
    print(f"   Headless mode: {headless}")
    print("-" * 80)
    
    try:
        with sync_playwright() as p:
            print("\n🌐 Launching browser...")
            browser = p.chromium.launch(
                headless=headless,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                locale='tr-TR'
            )
            
            page = context.new_page()
            page.set_default_timeout(30000)
            
            # Load homepage
            print(f"📡 Loading homepage: {url}")
            page.goto(url, wait_until='domcontentloaded')
            page.wait_for_timeout(3000)
            
            # Find article links
            print("\n🔍 Finding article links...")
            article_links = []
            all_links = page.query_selector_all('a[href]')
            
            seen_urls = set()
            for link in all_links:
                if len(article_links) >= num_articles * 2:  # Get more than needed
                    break
                
                href = link.get_attribute('href')
                if not href or href in seen_urls:
                    continue
                
                # Filter for article URLs
                if href.startswith('/haber/') or href.startswith('/yazarlar/') or '/haber/' in href:
                    seen_urls.add(href)
                    
                    # Make absolute URL
                    if href.startswith('/'):
                        full_url = f"https://haber.sol.org.tr{href}"
                    else:
                        full_url = href
                    
                    article_links.append(full_url)
            
            print(f"   ✅ Found {len(article_links)} article links")
            
            # Extract full text from each article
            print(f"\n📰 Extracting full text from articles...")
            print("-" * 80)
            
            for i, article_url in enumerate(article_links[:num_articles * 2], 1):
                if len(articles) >= num_articles:
                    break
                
                print(f"\n   Article {len(articles) + 1}:")
                article_data = extract_article_fulltext(page, article_url)
                
                if article_data and article_data.get('full_text'):
                    articles.append(article_data)
                else:
                    print(f"      ⏭️  Skipping (no content)")
            
            browser.close()
            
            print(f"\n{'=' * 80}")
            print(f"✅ Successfully extracted {len(articles)} articles with full text")
            print("=" * 80)
            
            return articles
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return []


def main():
    # Fetch articles
    articles = fetch_articles_fulltext(num_articles=4, headless=True)
    
    if not articles:
        print("\n❌ No articles extracted!")
        return 1
    
    # Display results
    print("\n" + "=" * 80)
    print("EXTRACTED ARTICLES")
    print("=" * 80)
    
    for i, article in enumerate(articles, 1):
        print(f"\n📰 ARTICLE {i}")
        print(f"Title: {article['title']}")
        print(f"Author: {article.get('author', 'N/A')}")
        print(f"Date: {article.get('date', 'N/A')}")
        print(f"URL: {article['url']}")
        print(f"Words: {article['word_count']} | Characters: {article['char_count']}")
        print(f"\nFirst 300 characters:")
        print("-" * 80)
        print(article['full_text'][:300] + "...")
        print("-" * 80)
    
    # Save full details
    output_file = 'sol_articles_fulltext.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Full data saved to: {output_file}")
    
    # Save text-only version for TTS
    text_only = []
    for article in articles:
        text_only.append({
            'title': article['title'],
            'text': article['full_text'],
            'url': article['url']
        })
    
    tts_file = 'sol_articles_for_tts.txt'
    with open(tts_file, 'w', encoding='utf-8') as f:
        for i, article in enumerate(articles, 1):
            f.write(f"HABER {i}: {article['title']}\n")
            f.write("=" * 80 + "\n\n")
            f.write(article['full_text'])
            f.write("\n\n" + "=" * 80 + "\n\n")
    
    print(f"💾 TTS text file saved to: {tts_file}")
    
    print("\n" + "=" * 80)
    print("✅ SUCCESS! Full article texts extracted and ready.")
    print("=" * 80)
    
    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
