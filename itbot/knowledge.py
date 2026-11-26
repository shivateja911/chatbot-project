"""Small BM25 keyword search over the Markdown articles in kb/."""
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

STOPWORDS = set(
    "a an and are as at be but by can cannot do does for from get got how i if in is it "
    "me my not of on or our so that the this to up was we what when with you your".split()
)


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("wi-fi", "wifi"))
    # Crude plural stripping so "printers" matches "printer".
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w
            for w in words if w not in STOPWORDS]


@dataclass
class Article:
    id: str
    title: str
    body: str
    terms: Counter


class KnowledgeBase:
    def __init__(self, folder: Path):
        self.articles: list[Article] = []
        for path in sorted(folder.glob("*.md")):
            body = path.read_text(encoding="utf-8")
            title = body.splitlines()[0].lstrip("# ").strip()
            tags = " ".join(re.findall(r"^Tags:(.*)$", body, re.M))
            # Title and tags are counted extra so they weigh more than body text.
            terms = Counter(tokenize(f"{body} {title} {title} {tags}"))
            self.articles.append(Article(path.stem, title, body, terms))
        if not self.articles:
            raise ValueError(f"No articles found in {folder}")
        self.avg_len = sum(sum(a.terms.values()) for a in self.articles) / len(self.articles)
        self.doc_freq = Counter(t for a in self.articles for t in a.terms)

    def search(self, query: str, limit: int = 3) -> list[Article]:
        n, scored = len(self.articles), []
        for article in self.articles:
            length = sum(article.terms.values())
            score = 0.0
            for term in set(tokenize(query)):
                tf = article.terms.get(term, 0)
                if tf:
                    idf = math.log(1 + (n - self.doc_freq[term] + 0.5) / (self.doc_freq[term] + 0.5))
                    score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / self.avg_len))
            if score > 0:
                scored.append((score, article))
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [article for _, article in scored[:limit]]
