# -*- coding: utf-8 -*-
"""
Created on Sun Feb 19 17:29:40 2023

@author: NitroLab
"""

import yaml
from ipaddress import IPv4Address, ip_address
from requests import RequestException, get
from pathlib import PurePath

PUBLIC_IP_URLS = [
    "https://api.ip.pe.kr/json/",
    "https://api.my-ip.io/ip",
    "https://api.myip.com",
    "https://api.ipify.org",
]


def parse_public_ip_response(response):
    text = response.text.strip()
    content_type = response.headers.get("content-type", "")
    if "json" in content_type or text.startswith("{"):
        data = response.json()
        if isinstance(data, dict) and data.get("ip"):
            return str(data["ip"]).strip()
    return text


def validate_public_ip(value):
    parsed = ip_address(value)
    if not isinstance(parsed, IPv4Address):
        raise ValueError("Expected an IPv4 address, got {}".format(value))
    return str(parsed)


def get_public_ip(urls=PUBLIC_IP_URLS, timeout=3):
    errors = []
    for url in urls:
        try:
            response = get(url, timeout=timeout)
            response.raise_for_status()
            ip = validate_public_ip(parse_public_ip_response(response))
            if ip:
                return ip
        except (RequestException, ValueError, KeyError) as exc:
            errors.append("{}: {}".format(url, exc))
    raise RuntimeError("Could not get public IP from configured providers: {}".format("; ".join(errors)))


class GallKeeperConfig():
    def __init__(self, file_config):
        self.get_config(file_config)
    
    def get_config(self, file_config):
        with open(file_config, 'r', encoding="utf-8") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)

        self.repeat = config['repeat']
        self.use_cache  = config['use_cache']
        
        self.board_id = config['board_id']
        self.board_name = config['board_name']
        self.board_minor = config['board_minor']
        
        self.nick = config['nick']
        self.author = self.get_author(self.nick)
        self.password = config['password']

        self.doc_write = config['doc_write']
        self.doc_title = config['doc_title']
        self.doc_contents_file = config['doc_contents']

        self.doc_watch = config['doc_watch']
        self.doc_watch_keywords = config['doc_watch_keywords']

        self.comment_write = config['comment_write']
        self.comment_contents_file = config['comment_contents']

        p = PurePath(file_config)
        f = open(p.parent / self.doc_contents_file, encoding="utf-8")
        self.doc_contents = f.read()
        f = open(p.parent / self.comment_contents_file, encoding="utf-8")
        self.comment_contents = f.read()
        f.close()
        
        self.mirror = config['mirror']
        self.mirror_target_board_id = config['mirror_target_board_id']
        self.mirror_target_board_minor = config['mirror_target_board_minor']
        self.mirror_cache_file = config.get('mirror_cache_file', 'caches/mirror_cache.sqlite')
        self.mirror_cleanup = config.get('mirror_cleanup', False)
        self.mirror_cleanup_delete = config.get('mirror_cleanup_delete', False)
        self.mirror_cleanup_recent = config.get('mirror_cleanup_recent', 10)
        self.mirror_cleanup_missing_cycles = config.get('mirror_cleanup_missing_cycles', 2)
        self.mirror_cleanup_scan_pages = config.get('mirror_cleanup_scan_pages', 3)
        self.mirror_cleanup_target_scan_pages = config.get('mirror_cleanup_target_scan_pages', self.mirror_cleanup_scan_pages)
        self.mirror_sync_update = config.get('mirror_sync_update', False)
        self.mirror_sync_update_apply = config.get('mirror_sync_update_apply', False)
        self.mirror_relevance_monitor = config.get('mirror_relevance_monitor', False)
        self.mirror_relevance_filter = config.get('mirror_relevance_filter', False)
        self.mirror_relevance_pass_score = config.get('mirror_relevance_pass_score', 7)
        self.mirror_relevance_review_score = config.get('mirror_relevance_review_score', 1)
        self.mirror_relevance_title_multiplier = config.get('mirror_relevance_title_multiplier', 2)
        self.mirror_relevance_contents_multiplier = config.get('mirror_relevance_contents_multiplier', 1)
        self.mirror_relevance_positive_keywords = config.get('mirror_relevance_positive_keywords', {})
        self.mirror_relevance_negative_keywords = config.get('mirror_relevance_negative_keywords', {})
        self.mirror_relevance_context_keywords = config.get('mirror_relevance_context_keywords', {})
        self.write_guard_file = config.get('write_guard_file', 'caches/write_guard.sqlite')
        self.write_guard_default_pause_minutes = config.get('write_guard_default_pause_minutes', 30)
        self.alert_event_file = config.get('alert_event_file', 'caches/alerts.jsonl')
        self.alert_repeat_seconds = config.get('alert_repeat_seconds', 1800)
        self.moderation_monitor = config.get('moderation_monitor', False)
        self.moderation_scan_comments = config.get('moderation_scan_comments', False)
        self.moderation_cache_file = config.get('moderation_cache_file', 'caches/moderation_candidates.sqlite')
        self.moderation_rules_file = config.get('moderation_rules_file', None)
        self.moderation_rules = config.get('moderation_rules', [])
        self.moderation_auto_action = config.get('moderation_auto_action', False)
        self.moderation_auto_action_cookie_file = config.get(
            'moderation_auto_action_cookie_file',
            'conf/dcinside_cookies.json',
        )
        self.moderation_auto_action_allow_galleries = config.get(
            'moderation_auto_action_allow_galleries',
            [],
        )
        self.moderation_auto_action_limit_per_cycle = config.get(
            'moderation_auto_action_limit_per_cycle',
            1,
        )
        self.moderation_auto_action_limit_per_day = config.get(
            'moderation_auto_action_limit_per_day',
            10,
        )
        self.moderation_auto_action_session_retry_seconds = config.get(
            'moderation_auto_action_session_retry_seconds',
            60,
        )
        if self.moderation_rules_file:
            rules_path = p.parent / self.moderation_rules_file
            with open(rules_path, 'r', encoding="utf-8") as f:
                rules_config = yaml.load(f, Loader=yaml.FullLoader) or {}
            self.moderation_rules.extend(rules_config.get('moderation_rules', []))

        return
    
    def get_author(self, nick):
        ip = get_public_ip()
        ip_head = '.'.join(ip.split('.')[0:2])
        author = nick+"("+ip_head+")"
        return author
    
if __name__ == "__main__":
    config = GallKeeperConfig("conf/default.yaml")
    doc = config.doc_contents
