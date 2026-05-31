# -*- coding: utf-8 -*-
"""
用 YouTube Data API v3 批量抓取视频的播放量和点赞数。
一次最多查 50 个视频，所以即使多场多成员也只需很少配额。
"""
import os
import requests

API_URL = "https://www.googleapis.com/youtube/v3/videos"


def fetch_stats(video_ids):
    """
    输入视频ID列表，返回 {video_id: {"views": int, "likes": int, "published": str}}
    点赞数可能被创作者隐藏，此时 likes 为 None。
    """
    api_key = os.environ["YOUTUBE_API_KEY"]
    result = {}
    # 分批，每批 50 个
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        params = {
            "part": "statistics,snippet",
            "id": ",".join(batch),
            "key": api_key,
        }
        r = requests.get(API_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            vid = item["id"]
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            views = stats.get("viewCount")
            likes = stats.get("likeCount")
            views_int = int(views) if views is not None else None
            likes_int = int(likes) if likes is not None else None
            # 防 API 偶发抽风：有播放量却 likeCount=0 或缺失，几乎一定是接口没返回，
            # 不是真的 0 赞 → 记为缺失(None)，写入时存空，图上该点跳过，不污染趋势。
            if likes_int == 0 and views_int and views_int > 0:
                likes_int = None
            result[vid] = {
                "views": views_int,
                "likes": likes_int,
                "published": snippet.get("publishedAt"),
                "title": snippet.get("title", ""),
            }
    return result
