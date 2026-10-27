"""
Website Generator - Creates HTML page with audio players
Generates a responsive website displaying articles with audio players

Usage:
    python generate_website.py
"""

import os
import json
import sys
from datetime import datetime

def generate_html(articles, output_file='website/index.html'):
    """Generate HTML website with audio players"""
    
    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>soL News - Audio Articles</title>
    <style>
        * {{{{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}}}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        header {{
            text-align: center;
            color: white;
            padding: 40px 20px;
            margin-bottom: 30px;
        }}
        
        header h1 {{
            font-size: 3em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }}
        
        header p {{
            font-size: 1.2em;
            opacity: 0.9;
        }}
        
        .stats {{
            background: rgba(255, 255, 255, 0.2);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 20px;
            margin-bottom: 30px;
            display: flex;
            justify-content: space-around;
            flex-wrap: wrap;
            gap: 20px;
        }}
        
        .stat-item {{
            color: white;
            text-align: center;
        }}
        
        .stat-number {{
            font-size: 2.5em;
            font-weight: bold;
            display: block;
        }}
        
        .stat-label {{
            font-size: 0.9em;
            opacity: 0.9;
        }}
        
        .articles-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 25px;
            margin-bottom: 40px;
        }}
        
        .article-card {{
            background: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }}
        
        .article-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 15px 40px rgba(0,0,0,0.3);
        }}
        
        .article-number {{
            display: inline-block;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 0.9em;
            font-weight: bold;
            margin-bottom: 15px;
        }}
        
        .article-title {{
            font-size: 1.4em;
            color: #333;
            margin-bottom: 15px;
            line-height: 1.4;
            font-weight: 600;
        }}
        
        .article-meta {{
            display: flex;
            gap: 15px;
            margin-bottom: 15px;
            flex-wrap: wrap;
            font-size: 0.9em;
            color: #666;
        }}
        
        .meta-item {{
            display: flex;
            align-items: center;
            gap: 5px;
        }}
        
        .meta-item::before {{
            content: '•';
            color: #667eea;
            font-weight: bold;
        }}
        
        .audio-player {{
            margin: 20px 0;
        }}
        
        audio {{
            width: 100%;
            height: 40px;
            border-radius: 20px;
            outline: none;
        }}
        
        audio::-webkit-media-controls-panel {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }}
        
        .article-links {{
            display: flex;
            gap: 10px;
            margin-top: 15px;
            flex-wrap: wrap;
        }}
        
        .btn {{
            padding: 10px 20px;
            border-radius: 20px;
            text-decoration: none;
            font-size: 0.9em;
            font-weight: 600;
            transition: all 0.3s ease;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }}
        
        .btn-primary {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        
        .btn-primary:hover {{
            transform: scale(1.05);
            box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
        }}
        
        .btn-secondary {{
            background: #f0f0f0;
            color: #333;
        }}
        
        .btn-secondary:hover {{
            background: #e0e0e0;
        }}
        
        footer {{
            text-align: center;
            color: white;
            padding: 30px 20px;
            margin-top: 40px;
        }}
        
        footer p {{
            opacity: 0.9;
            margin: 5px 0;
        }}
        
        @media (max-width: 768px) {{
            header h1 {{
                font-size: 2em;
            }}
            
            .articles-grid {{
                grid-template-columns: 1fr;
            }}
            
            .stat-number {{
                font-size: 2em;
            }}
        }}
        
        .loading {{
            text-align: center;
            padding: 40px;
            color: white;
        }}
        
        .no-articles {{
            background: white;
            border-radius: 15px;
            padding: 40px;
            text-align: center;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🎙️ soL News</h1>
            <p>Audio News Platform</p>
        </header>
        
        <div class="stats">
            <div class="stat-item">
                <span class="stat-number">{article_count}</span>
                <span class="stat-label">Articles</span>
            </div>
            <div class="stat-item">
                <span class="stat-number">{total_words}</span>
                <span class="stat-label">Words</span>
            </div>
            <div class="stat-item">
                <span class="stat-number">{audio_count}</span>
                <span class="stat-label">With Audio</span>
            </div>
        </div>
        
        <div class="articles-grid">
            {articles_html}
        </div>
        
        <footer>
            <p><strong>soL News Audio Platform</strong></p>
            <p>Articles are sourced from haber.sol.org.tr</p>
            <p>Generated: {generated_time}</p>
        </footer>
    </div>
    
    <script>
        // Add smooth scroll
        document.querySelectorAll('a[href^="#"]').forEach(anchor => {{
            anchor.addEventListener('click', function (e) {{
                e.preventDefault();
                document.querySelector(this.getAttribute('href')).scrollIntoView({{
                    behavior: 'smooth'
                }});
            }});
        }});
        
        // Track audio plays
        document.querySelectorAll('audio').forEach(audio => {{
            audio.addEventListener('play', function() {{
                console.log('Playing:', this.getAttribute('data-title'));
            }});
        }});
    </script>
</body>
</html>"""

    # Generate article cards HTML
    articles_html = ""
    total_words = 0
    audio_count = 0

    for article in articles:
        total_words += article.get('word_count', 0)
        if article.get('audio_generated', False):
            audio_count += 1

        # Create audio player HTML if audio exists
        audio_html = ""
        if article.get('audio_generated', False):
            audio_html = f"""
            <div class="audio-player">
                <audio controls data-title="{article['title']}">
                    <source src="../{article['audio_file']}" type="audio/mpeg">
                    Your browser does not support the audio element.
                </audio>
            </div>
            """
        else:
            audio_html = """
            <div class="audio-player">
                <p style="color: #999; font-style: italic;">🔇 Audio not generated yet</p>
            </div>
            """

        article_html = f"""
        <div class="article-card">
            <span class="article-number">Article #{article['id']}</span>
            <h2 class="article-title">{article['title']}</h2>
            <div class="article-meta">
                <span class="meta-item">👤 {article.get('author', 'Unknown')}</span>
                <span class="meta-item">📅 {article.get('date', 'No date')}</span>
                <span class="meta-item">📝 {article.get('word_count', 0)} words</span>
            </div>
            {audio_html}
            <div class="article-links">
                <a href="{article['url']}" target="_blank" class="btn btn-primary">
                    📰 Read Article
                </a>
                <a href="../{article['txt_file']}" target="_blank" class="btn btn-secondary">
                    📄 Text
                </a>
            </div>
        </div>
        """
        articles_html += article_html

    if not articles_html:
        articles_html = """
        <div class="no-articles">
            <h2>No articles yet</h2>
            <p>The scraper has not run, or found no articles.</p>
        </div>
        """

    # Fill template
    html = html_template.format(
        article_count=len(articles),
        total_words=f"{total_words:,}",
        audio_count=audio_count,
        articles_html=articles_html,
        generated_time=datetime.now().strftime('%d.%m.%Y %H:%M')
    )

    return html

def create_website(articles_dir='articles'):
    """Create website from articles"""

    print("=" * 80)
    print("Website Generator")
    print("=" * 80)
    print(f"\n📁 Input directory: {articles_dir}/")
    print("-" * 80)

    # Load index
    index_path = os.path.join(articles_dir, 'index.json')
    if not os.path.exists(index_path):
        print(f"\n❌ Index file not found: {index_path}")
        print("   Run scraper.py first!")
        return False

    with open(index_path, 'r', encoding='utf-8') as f:
        articles = json.load(f)

    print(f"\n📰 Found {len(articles)} articles")

    # Create website directory
    website_dir = os.path.join(articles_dir, 'website')
    os.makedirs(website_dir, exist_ok=True)

    # Generate HTML
    print("🔨 Generating HTML...")
    html = generate_html(articles)

    # Save HTML
    output_path = os.path.join(website_dir, 'index.html')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✅ Website generated: {output_path}")

    # Get absolute path
    abs_path = os.path.abspath(output_path)

    print(f"\n{'=' * 80}")
    print("✅ Website ready!")
    print(f"📂 Location: {abs_path}")
    print(f"\n🌐 Open in browser:")
    print(f"   file:///{abs_path.replace(chr(92), '/')}")
    print("=" * 80)

    return True

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Generate website with audio players')
    parser.add_argument('--dir', type=str, default='articles',
                       help='Articles directory (default: articles)')

    args = parser.parse_args()

    success = create_website(args.dir)
    return 0 if success else 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)