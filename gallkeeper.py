# -*- coding: utf-8 -*-
"""
Created on Wed Jan 26 03:53:10 2022

@author: NitroLab
"""

from pathlib import Path
import sys
import json

API_REPO_DIR = Path(__file__).resolve().parents[1] / "api_repo"
if API_REPO_DIR.is_dir():
    sys.path.insert(0, str(API_REPO_DIR))

import dc_api
import asyncio
import pandas as pd
import requests
from types import SimpleNamespace
import time
from datetime import datetime
from gallkeeper_cfg import GallKeeperConfig, get_public_ip
from dc_board import Board, split_author_display
from dc_comments import Comments
from mirror_cache import MirrorCache
from moderation import ModerationMatcher
from write_guard import WriteGuard
from moderation_action import (
    ENDPOINT as MODERATION_ACTION_ENDPOINT,
    PC_HEADERS,
    load_cookie_rows,
    import_cookies,
    manager_marker_probe,
    quick_conf_probe,
    prepare_payload,
    submit_action,
    response_result,
)
from moderation_cache import ModerationCandidateCache
from relevance import RelevanceScorer
from time import strftime

class GallKeeper():
    """
    https://gall.dcinside.com/mgallery/board/view/?id=improvisation&no=1084

    1. [x] 주기적으로 지정된 게시판 읽기
        1. [x] 글 목록은 모바일 기준 1페이지 이내로 읽기 권고.
        2. [x] 읽기 후 가장 최근 작성된 글 번호 저장
    2. [x] 지정된 갤러리에 모바일 기준 1페이지 이내 (8개)에 봇글이 없는 경우, 지정된 글 게시
        1. [x] 봇글 존재 여부는 지정된 작성자 존재 여부로 파악한다.
        * (봇 전용 닉 지정 권고)
        2. [x] 봇글의 타이틀과 작성자, 게시 내용, 비밀번호는 저장된 설정 파일을 이용.
        3. [x] 봇글이 없는 경우 봇글 게시.
    3. [x] 지정된 갤러리에 지정된 키워드가 포함된 글이 올라올 시 지정된 댓글을 남김
        * 예: 포락갤에 문자열 '재즈'가 포함된 글이 게시될 경우 봇이 즉흥갤 링크 댓글 작성
        1. [x] 해당 글에 봇 전용 닉의 댓글 존재 여부 확인 후, 댓글이 없는 경우 즉시 댓글 작성.
        2. [x] 댓글의 작성자, 게시 내용, 비밀번호는 저장된 설정 파일을 이용.
        3. [x] 봇의 게시글은 제외
    4. [ ] 지정된 갤러리에 지정된 키워드가 올라오면 지정된 갤러리에 원글 출처와 함께 미러링
        * 예: 포락갤에 문자열 '재즈'가 포함된 글이 게시될 경우, 봇이 즉흥갤에 해당글을 포락갤 링크와 함께 게시
        1. [ ] 봇글의 작성자, 게시 내용은 원글을 따름
        2. [x] 봇글의 타이틀 앞에 출처 기록
        * 예: (재즈갤|80090) 재즈피아노 입문자, 마이너 스케일 관련 질문
        3. [x] 비밀번호는 저장된 설정 파일을 이용
        4. [x] 읽기 후 가장 최근 작성된 글 번호 저장
        5. [ ] https://github.com/dr-nitro-lab/dc-gallkeeper/issues/1 원문 추출 시 기본으로 생성되는 코드 제거
        6. [ ] 봇의 게시글은 제외
    
    """
    def __init__(self, file_config, dry_run=False):
        self.file_config = file_config
        self.dry_run = dry_run
        self.doc_write_use_gallery_nickname = False
        self.doc_write_html_memo = False
        self.doc_write_backend = "mobile"
        self.doc_write_pc_use_html = False
        self.mirror_cache = None
        self.moderation_cache = None
        self.moderation_matcher = None
        self.relevance_scorer = None
        self.write_guard = None
        self.write_pause_notice_keys = set()
        self.alert_notice_times = {}
        self.moderation_auto_action_paused_until = 0
        self.get_config(file_config)

    async def run(self, max_cycles=None, interval_seconds=10):
        cycle = 0
        while(True):
            cycle += 1
            # print(strftime('%Y.%m.%d %H:%M:%S'))
            try:
                await self.get_board()
            except Exception as exc:
                print("({}) getting board data failed: {!r}".format(self.board_id, exc))
                if max_cycles is not None:
                    raise
                await asyncio.sleep(interval_seconds)
                continue
            
            if(self.doc_write):
                if(not self.has_doc_write_post()):
                    if self.print_write_paused_once(
                        "write_document",
                        self.board_id,
                        "({}) writing doc ... ".format(self.board_id),
                    ):
                        pass
                    elif self.dry_run:
                        print("({}) writing doc ... ".format(self.board_id), end="")
                        write_name = self.get_doc_write_name()
                        write_contents = self.get_doc_write_contents()
                        print("[dry-run] would write doc backend={} name={!r} title={!r} contents_len={}".format(
                            self.doc_write_backend,
                            write_name,
                            self.doc_title,
                            len(write_contents),
                        ))
                    else:
                        print("({}) writing doc ... ".format(self.board_id), end="")
                        try:
                            doc_id = await self.write_document()
                            print("done doc_id={}".format(doc_id))
                        except Exception as exc:
                            print("failed: {!r}".format(exc))
                            if max_cycles is not None:
                                raise
                else:
                    print("({}) writing doc ... wait!".format(self.board_id))

            if self.has_moderation_rules():
                get_contents = True
                last_id = self.moderation_last_id
                try:
                    await self.get_board(self.board_id, get_contents, self.moderation_last_id)
                except Exception as exc:
                    print("({}) moderation scan failed: {!r}".format(self.board_id, exc))
                    if max_cycles is not None:
                        raise
                    await asyncio.sleep(interval_seconds)
                    continue
                if len(self.board.df_board) > 0:
                    last_id = self.board.df_board["id"].iloc[0]
                print("({}) moderation scan ... ".format(self.board_id), end="")
                for idx, row in self.board.df_board.iterrows():
                    if row.id <= self.moderation_last_id:
                        break
                    await self.record_moderation_candidates(row)
                self.run_auto_moderation_actions()
                self.moderation_last_id = last_id
                print("done last_id={}".format(self.moderation_last_id))
            
            if(self.doc_watch):
                get_contents=True
                failed=False
                last_id = self.board.df_board["id"].iloc[0]
                try:
                    await self.get_board(self.board_id, get_contents, self.doc_watch_last_id)
                except Exception as exc:
                    print("({}) watching keywords failed: {!r}".format(self.board_id, exc))
                    if max_cycles is not None:
                        raise
                    await asyncio.sleep(interval_seconds)
                    continue
                df_watch = self.board.findContentsNTitle(self.doc_watch_keywords)
                # print(df_watch)
                print("({}) watching keywords ... matched={}".format(self.board_id, len(df_watch)))
                for idx, row in df_watch.iterrows():
                    if(row.id <= self.doc_watch_last_id):
                        break
                    if(self.comment_write):
                        print("({}) ({}) comment ... "\
                              .format(self.board_id, row.id), end="")
                        if(row.author == self.author):
                            print("(gallkeeper-generated doc)")
                        else:
                            await self.get_comments(self.board_id, row.id)
                            if(self.comments.isAuthorExists(self.author)):
                                print("already done")
                            else:
                                if self.print_write_paused_once("write_comment", self.board_id):
                                    pass
                                elif self.dry_run:
                                    print("[dry-run] would write comment")
                                else:
                                    try:
                                        await self.write_comment(row.id)
                                    except Exception as exc:
                                        print("failed: {!r}".format(exc))
                                        self.comment_write=False
                                        failed=True
                                    print("done")
                    if(self.mirror):
                        print("({}) ({}) -> ({}) mirror ... "\
                              .format(self.board_id, row.id,
                                      self.mirror_target_board_id), end="")
                        relevance = self.score_mirror_relevance(row)
                        title = self.mirror_title(row.title)
                        author = row.author
                        contents = self.mirror_contents(row.id)

                        if(row.author == self.author):
                            print("(gallkeeper-generated doc)")
                        elif self.should_skip_mirror_by_relevance(relevance):
                            print("skipped relevance score={} decision={}".format(
                                relevance["score"],
                                relevance["decision"],
                            ))
                        elif self.has_mirrored_source(row.id):
                            print("already mirrored")
                        else:
                            existing_mirror_doc_id = await self.find_existing_mirror_document_id(title)
                            if existing_mirror_doc_id is not None:
                                if not self.dry_run:
                                    self.record_mirror(row.id, row.title, existing_mirror_doc_id, title)
                                print("already mirrored doc_id={}".format(existing_mirror_doc_id))
                                print("done")
                                continue
                            if self.print_write_paused_once("mirror_write", self.mirror_target_board_id):
                                pass
                            elif self.dry_run:
                                print("[dry-run] would mirror author={!r} title={!r} contents={!r}".format(author, title, contents))
                            else:
                                try:
                                    mirror_doc_id = await self.write_document(
                                        self.mirror_target_board_id,
                                        self.mirror_target_board_minor,
                                        author, title, contents)
                                    self.record_mirror(row.id, row.title, mirror_doc_id, title)
                                except Exception as exc:
                                    print("failed: {!r}".format(exc))
                                    failed=True
                        print("done")
                # if(not failed):
                #     self.doc_watch_last_id = last_id
                self.doc_watch_last_id = last_id
                print("({}) last watched doc id: {}".format(self.board_id,
                                                            self.doc_watch_last_id))
            if self.mirror_cleanup:
                try:
                    await self.cleanup_mirrors()
                except Exception as exc:
                    print("({}) mirror cleanup failed: {!r}".format(self.board_id, exc))
                    if max_cycles is not None:
                        raise
            
            if max_cycles is not None and cycle >= max_cycles:
                break
            if(self.repeat == False):
                break
            await asyncio.sleep(interval_seconds)

    def get_config(self, file_config):
        self.cfg = GallKeeperConfig(file_config)

        self.repeat = self.cfg.repeat
        self.use_cache = self.cfg.use_cache

        self.board_id = self.cfg.board_id
        self.board_name = self.cfg.board_name
        self.board_minor = self.cfg.board_minor

        self.nick = self.cfg.nick
        self.author = self.cfg.author
        self.password = self.cfg.password
        
        self.board = Board(self.board_id)
        self.doc_write = self.cfg.doc_write
        self.doc_title = self.cfg.doc_title
        self.doc_contents = self.cfg.doc_contents

        self.doc_watch = self.cfg.doc_watch
        self.doc_watch_keywords = self.cfg.doc_watch_keywords
        self.doc_watch_last_id = 0

        self.comments = Comments()
        self.comment_write = self.cfg.comment_write
        self.comment_contents=self.cfg.comment_contents
        
        self.mirror = self.cfg.mirror
        self.mirror_target_board_id = self.cfg.mirror_target_board_id
        self.mirror_target_board_minor = self.cfg.mirror_target_board_minor
        self.mirror_cache_file = self.cfg.mirror_cache_file
        self.mirror_cleanup = self.cfg.mirror_cleanup
        self.mirror_cleanup_delete = self.cfg.mirror_cleanup_delete
        self.mirror_cleanup_recent = int(self.cfg.mirror_cleanup_recent)
        self.mirror_cleanup_missing_cycles = int(self.cfg.mirror_cleanup_missing_cycles)
        self.mirror_cleanup_scan_pages = int(self.cfg.mirror_cleanup_scan_pages)
        self.mirror_cleanup_target_scan_pages = int(self.cfg.mirror_cleanup_target_scan_pages)
        self.mirror_sync_update = bool(self.cfg.mirror_sync_update)
        self.mirror_sync_update_apply = bool(self.cfg.mirror_sync_update_apply)
        self.mirror_relevance_monitor = bool(self.cfg.mirror_relevance_monitor)
        self.mirror_relevance_filter = bool(self.cfg.mirror_relevance_filter)
        self.moderation_monitor = bool(self.cfg.moderation_monitor)
        self.moderation_scan_comments = bool(self.cfg.moderation_scan_comments)
        self.moderation_cache_file = self.cfg.moderation_cache_file
        self.moderation_last_id = 0
        self.moderation_auto_action = bool(self.cfg.moderation_auto_action)
        self.moderation_auto_action_cookie_file = self.cfg.moderation_auto_action_cookie_file
        self.moderation_auto_action_allow_galleries = self.cfg.moderation_auto_action_allow_galleries
        self.moderation_auto_action_limit_per_cycle = int(self.cfg.moderation_auto_action_limit_per_cycle)
        self.moderation_auto_action_limit_per_day = int(self.cfg.moderation_auto_action_limit_per_day)
        self.moderation_auto_action_session_retry_seconds = int(
            self.cfg.moderation_auto_action_session_retry_seconds
        )
        self.write_guard_file = self.cfg.write_guard_file
        self.write_guard = WriteGuard(
            self.write_guard_file,
            default_pause_minutes=self.cfg.write_guard_default_pause_minutes,
        )
        self.alert_event_file = self.cfg.alert_event_file
        self.alert_repeat_seconds = int(self.cfg.alert_repeat_seconds)
        if self.mirror or self.mirror_cleanup or self.mirror_relevance_monitor:
            self.mirror_cache = MirrorCache(self.mirror_cache_file)
        if self.moderation_monitor:
            self.moderation_cache = ModerationCandidateCache(self.moderation_cache_file)
            self.moderation_matcher = ModerationMatcher(self.cfg.moderation_rules)
        if self.mirror_relevance_monitor or self.mirror_relevance_filter:
            self.relevance_scorer = RelevanceScorer(
                self.doc_watch_keywords,
                positive_keywords=self.cfg.mirror_relevance_positive_keywords,
                negative_keywords=self.cfg.mirror_relevance_negative_keywords,
                context_keywords=self.cfg.mirror_relevance_context_keywords,
                title_multiplier=self.cfg.mirror_relevance_title_multiplier,
                contents_multiplier=self.cfg.mirror_relevance_contents_multiplier,
                review_score=self.cfg.mirror_relevance_review_score,
                pass_score=self.cfg.mirror_relevance_pass_score,
            )
        return

    def has_moderation_rules(self):
        if self.moderation_matcher is None:
            return False
        return self.moderation_matcher.has_rules()

    def require_write_allowed(self, action_type, board_id=None):
        if self.write_guard is None:
            return
        self.write_guard.require_allowed(action_type, board_id or self.board_id)

    def record_write_success(self, action_type, board_id=None):
        if self.write_guard is None:
            return
        self.write_guard.record_success(action_type, board_id or self.board_id)

    def record_write_failure(self, action_type, board_id, exc):
        if self.write_guard is None:
            return
        signal = self.write_guard.record_failure(action_type, board_id or self.board_id, exc)
        if signal.should_pause:
            self.write_pause_notice_keys.clear()
            print(
                "write guard paused category={} until={} ".format(
                    signal.category,
                    signal.pause_until,
                ),
                end="",
            )
            self.emit_alert(
                "write_guard_paused",
                "Bot write guard paused {} until {}".format(signal.category, signal.pause_until),
                {
                    "board_id": board_id or self.board_id,
                    "action_type": action_type,
                    "category": signal.category,
                    "pause_until": signal.pause_until,
                    "message": signal.message,
                },
            )

    def current_write_pause(self):
        if self.write_guard is None:
            return None
        return self.write_guard.current_pause()

    def print_write_paused_once(self, action_type, board_id=None, prefix=""):
        pause = self.current_write_pause()
        if pause is None:
            return False
        key = (
            action_type,
            board_id or self.board_id,
            pause["paused_until"],
            pause["category"],
        )
        if key not in self.write_pause_notice_keys:
            self.write_pause_notice_keys.add(key)
            print("{}paused until {} category={} message={!r}".format(
                prefix,
                pause["paused_until"],
                pause["category"],
                pause["message"],
            ))
        return True

    async def record_moderation_candidates(self, row):
        if self.moderation_cache is None or self.moderation_matcher is None:
            return
        if not self.moderation_matcher.has_rules():
            return
        candidates = self.moderation_matcher.match_article(self.board_id, row)
        if self.moderation_scan_comments and int(row.comment_count) > 0:
            comments_df = await self.get_comments(self.board_id, int(row.id))
            for idx, comment_row in comments_df.iterrows():
                candidates.extend(self.moderation_matcher.match_comment(
                    self.board_id,
                    int(row.id),
                    comment_row,
                    title=row.title,
                ))
        if not candidates:
            return
        result = self.moderation_cache.record_candidates(candidates)
        print("({}) moderation candidates new={} seen={} ".format(
            self.board_id,
            result["created"],
            result["updated"],
        ), end="")

    def run_auto_moderation_actions(self):
        if not self.moderation_auto_action or self.moderation_cache is None:
            return
        if self.moderation_auto_action_paused_until > time.time():
            return
        if self.board_id not in self.moderation_auto_action_allow_galleries:
            print("auto moderation skipped: gallery not allowed ", end="")
            return
        used_today = self.moderation_cache.action_count_since(
            mode="auto",
            since_sql="datetime('now', '-24 hours')",
        )
        remaining_today = self.moderation_auto_action_limit_per_day - used_today
        if remaining_today <= 0:
            print("auto moderation skipped: daily limit reached ", end="")
            return
        candidates = self.moderation_cache.pending_auto_candidates(
            board_id=self.board_id,
            limit=min(self.moderation_auto_action_limit_per_cycle, remaining_today),
        )
        if not candidates:
            return
        for candidate in candidates:
            try:
                self.run_auto_moderation_action(candidate)
            except Exception as exc:
                print("auto moderation failed candidate_id={}: {!r} ".format(
                    candidate["id"],
                    exc,
                ), end="")
                if self.is_manager_session_error(exc):
                    self.pause_auto_moderation_session(exc)
                    break

    def is_manager_session_error(self, exc):
        message = str(exc)
        return (
            "manager markers were not found" in message
            or "ci_c cookie was not found" in message
            or "정상적인 접근이 아닙니다" in message
        )

    def pause_auto_moderation_session(self, exc):
        seconds = max(1, self.moderation_auto_action_session_retry_seconds)
        self.moderation_auto_action_paused_until = time.time() + seconds
        print("auto moderation session paused {}s: {!r} ".format(seconds, exc), end="")
        self.emit_alert(
            "manager_session_error",
            "Manager moderation session is not usable",
            {
                "board_id": self.board_id,
                "retry_seconds": seconds,
                "error": str(exc),
            },
        )

    def emit_alert(self, kind, message, details=None):
        now = time.time()
        details = details or {}
        key = (kind, details.get("board_id", ""), details.get("category", ""))
        last_sent = self.alert_notice_times.get(key, 0)
        if now - last_sent < self.alert_repeat_seconds:
            return
        self.alert_notice_times[key] = now
        event = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "message": message,
            "details": details,
        }
        path = Path(self.alert_event_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def run_auto_moderation_action(self, candidate):
        if self.dry_run:
            print("[dry-run] would auto moderate candidate_id={} ".format(candidate["id"]), end="")
            return
        session = requests.Session()
        session.headers.update(PC_HEADERS)
        import_cookies(session, load_cookie_rows(self.moderation_auto_action_cookie_file))
        ci_t = session.cookies.get("ci_c", domain=".dcinside.com") or session.cookies.get("ci_c") or ""
        if not ci_t:
            raise RuntimeError("ci_c cookie was not found")
        referer, found_markers, article_status = manager_marker_probe(session, candidate)
        if article_status == 404:
            self.moderation_cache.audit_candidate_action(
                candidate["id"],
                mode="auto",
                endpoint=MODERATION_ACTION_ENDPOINT,
                target_no=candidate["comment_id"] if candidate["target_type"] == "comment" else candidate["article_id"],
                parent_no=candidate["article_id"] if candidate["target_type"] == "comment" else "",
                avoid_hour=candidate["avoid_hour"],
                avoid_reason=candidate["avoid_reason"],
                reason_text=candidate["reason_text"],
                del_chk=bool(candidate["del_chk"]),
                avoid_type_chk=bool(candidate["avoid_type_chk"]),
                status_code=article_status,
                result="source_missing",
                message="article page returned 404 before manager action",
                response_text="",
            )
            self.moderation_cache.mark_candidate_status(candidate["id"], "source_missing")
            print("auto moderation source_missing candidate_id={} ".format(candidate["id"]), end="")
            return
        if not found_markers:
            raise RuntimeError("manager markers were not found")
        quick_conf_probe(session, referer, ci_t, candidate, "M")
        action_args = SimpleNamespace(
            avoid_hour=candidate["avoid_hour"],
            avoid_reason=candidate["avoid_reason"],
            reason_text=candidate["reason_text"],
            delete=bool(candidate["del_chk"]),
            ip_block=bool(candidate["avoid_type_chk"]),
            galltype="M",
        )
        payload = prepare_payload(candidate, action_args, ci_t)
        status_code, response_text = submit_action(session, referer, payload)
        result, message = response_result(response_text)
        self.moderation_cache.audit_candidate_action(
            candidate["id"],
            mode="auto",
            endpoint=MODERATION_ACTION_ENDPOINT,
            target_no=candidate["comment_id"] if candidate["target_type"] == "comment" else candidate["article_id"],
            parent_no=candidate["article_id"] if candidate["target_type"] == "comment" else "",
            avoid_hour=candidate["avoid_hour"],
            avoid_reason=candidate["avoid_reason"],
            reason_text=candidate["reason_text"],
            del_chk=bool(candidate["del_chk"]),
            avoid_type_chk=bool(candidate["avoid_type_chk"]),
            status_code=status_code,
            result=result,
            message=message,
            response_text=response_text,
        )
        if status_code == 200 and result == "success":
            self.moderation_cache.mark_candidate_status(candidate["id"], "actioned")
            print("auto moderation actioned candidate_id={} ".format(candidate["id"]), end="")
        else:
            self.moderation_cache.mark_candidate_status(candidate["id"], "action_failed")
            raise RuntimeError("auto action failed status={} result={} message={}".format(
                status_code,
                result,
                message,
            ))

    def get_doc_write_name(self):
        if self.doc_write_use_gallery_nickname:
            return ""
        return self.nick

    def get_doc_write_contents(self):
        if not self.doc_write_html_memo:
            return self.doc_contents
        return self.doc_contents.replace('""', '"').strip().replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")

    def has_doc_write_post(self):
        if len(self.board.findAuthor(self.author)) > 0:
            return True
        if 'title' in self.board.df_board:
            return len(self.board.df_board[self.board.df_board['title'] == self.doc_title]) > 0
        return False

    def has_mirrored_source(self, source_doc_id):
        if self.mirror_cache is None:
            return False
        return self.mirror_cache.has_handled_source(
            self.board_id,
            int(source_doc_id),
            self.mirror_target_board_id,
        )

    def record_mirror(self, source_doc_id, source_title, mirror_doc_id, mirror_title):
        if self.mirror_cache is None or mirror_doc_id is None:
            return
        self.mirror_cache.record_mirror(
            self.board_id,
            int(source_doc_id),
            source_title,
            self.mirror_target_board_id,
            int(mirror_doc_id),
            mirror_title,
            self.mirror_target_board_minor,
            self.password,
        )

    def mirror_title(self, source_title):
        return "[{}] {}".format(self.board_name, source_title)

    def mirror_contents(self, source_doc_id):
        url = "https://m.dcinside.com/board/{}/{}".format(self.board_id, source_doc_id)
        return "출처: " + url

    def score_mirror_relevance(self, row):
        if self.relevance_scorer is None:
            return None
        result = self.relevance_scorer.score(row.title, row.contents)
        if self.mirror_relevance_monitor and self.mirror_cache is not None:
            self.mirror_cache.record_relevance(
                self.board_id,
                int(row.id),
                row.title,
                self.mirror_target_board_id,
                result,
                filter_applied=self.mirror_relevance_filter,
            )
        print("relevance score={} decision={} ".format(
            result["score"],
            result["decision"],
        ), end="")
        return result

    def should_skip_mirror_by_relevance(self, result):
        if result is None or not self.mirror_relevance_filter:
            return False
        return result["decision"] == "skip"

    async def find_existing_mirror_document_id(self, title, num=80):
        async with dc_api.API() as api:
            async for index in api.board(board_id=self.mirror_target_board_id, num=num):
                if index.title == title:
                    return int(index.id)
        return None

    async def cleanup_mirrors(self):
        if self.mirror_cache is None:
            return
        rows = self.mirror_cache.recent_active_by_source(
            self.board_id,
            self.mirror_cleanup_recent,
        )
        if not rows:
            return
        target_ids_by_board = {}
        for row in rows:
            target_board_id = row["target_board_id"]
            if target_board_id not in target_ids_by_board:
                target_ids_by_board[target_board_id] = await self.list_board_document_indexes(
                    target_board_id,
                    num=max(1, self.mirror_cleanup_target_scan_pages) * 8,
                )
        visible_source_indexes = await self.list_board_document_indexes(
            self.board_id,
            num=max(1, self.mirror_cleanup_scan_pages) * 8,
        )
        visible_source_ids = set(visible_source_indexes)
        if not visible_source_ids:
            return
        min_visible_source_id = min(visible_source_ids)
        max_visible_source_id = max(visible_source_ids)
        for row in rows:
            target_indexes = target_ids_by_board.get(row["target_board_id"], {})
            target_ids = set(target_indexes)
            if target_ids:
                target_doc_id = int(row["target_doc_id"])
                min_visible_target_id = min(target_ids)
                max_visible_target_id = max(target_ids)
                if target_doc_id in target_ids:
                    row["target_title"] = target_indexes[target_doc_id].title
                    self.mirror_cache.mark_target_seen(row["id"], row["target_title"])
                elif target_doc_id < min_visible_target_id:
                    print("({}) mirror target out of scanned range target_doc_id={} scanned={}..{}".format(
                        self.board_id,
                        target_doc_id,
                        min_visible_target_id,
                        max_visible_target_id,
                    ))
                else:
                    target_missing_count = self.mirror_cache.mark_target_missing(row["id"])
                    print("({}) mirror target missing target_doc_id={} missing_count={}".format(
                        self.board_id,
                        target_doc_id,
                        target_missing_count,
                    ))
                    if target_missing_count >= self.mirror_cleanup_missing_cycles:
                        self.mirror_cache.mark_removed_external(row["id"])
                        print("({}) mirror target marked removed_external target_doc_id={}".format(
                            self.board_id,
                            target_doc_id,
                        ))
                    continue
            source_doc_id = int(row["source_doc_id"])
            if source_doc_id in visible_source_ids:
                source_title = visible_source_indexes[source_doc_id].title
                self.mirror_cache.mark_seen(row["id"], source_title)
                await self.sync_mirror_update(row, source_title)
                continue
            if source_doc_id < min_visible_source_id:
                print("({}) mirror source out of scanned range source_doc_id={} scanned={}..{}".format(
                    self.board_id,
                    source_doc_id,
                    min_visible_source_id,
                    max_visible_source_id,
                ))
                continue
            missing_count = self.mirror_cache.mark_missing(row["id"])
            print("({}) mirror source missing source_doc_id={} mirror_doc_id={} missing_count={}".format(
                self.board_id,
                source_doc_id,
                row["target_doc_id"],
                missing_count,
            ))
            if missing_count < self.mirror_cleanup_missing_cycles:
                continue
            if self.dry_run or not self.mirror_cleanup_delete:
                print("[dry-run] would remove mirror doc_id={} from {}".format(
                    row["target_doc_id"],
                    row["target_board_id"],
                ))
                continue
            try:
                await self.remove_document(
                    row["target_board_id"],
                    int(row["target_doc_id"]),
                    bool(row["target_is_minor"]),
                    row["password"],
                )
                self.mirror_cache.mark_removed(row["id"])
                print("removed mirror doc_id={} from {}".format(
                    row["target_doc_id"],
                    row["target_board_id"],
                ))
            except Exception as exc:
                print("remove mirror failed doc_id={}: {!r}".format(row["target_doc_id"], exc))

    async def sync_mirror_update(self, row, source_title):
        if not self.mirror_sync_update:
            return
        expected_title = self.mirror_title(source_title)
        current_title = row["target_title"] or ""
        if current_title == expected_title:
            return
        if self.dry_run or not self.mirror_sync_update_apply:
            print("[dry-run] would update mirror doc_id={} title={!r} -> {!r}".format(
                row["target_doc_id"],
                current_title,
                expected_title,
            ))
            return
        try:
            await self.modify_document(
                row["target_board_id"],
                int(row["target_doc_id"]),
                expected_title,
                "",
                row["password"],
            )
            self.mirror_cache.mark_updated(row["id"], source_title, expected_title)
            print("updated mirror doc_id={} title={!r}".format(
                row["target_doc_id"],
                expected_title,
            ))
        except Exception as exc:
            print("update mirror failed doc_id={}: {!r}".format(row["target_doc_id"], exc))

    def get_author(self, nick):
        ip = get_public_ip()
        ip_head = '.'.join(ip.split('.')[0:2])
        author = nick+"("+ip_head+")"
        return author
    
    async def get_board(self, board_id=None, get_contents=False, last_id=0):
        board_id = self.board_id if board_id is None else board_id
        print("({}) getting board data ... ".format(board_id), end="")
        cols = [
            'id', 'author', 'author_name', 'author_ip', 'author_id',
            'time', 'title', 'comment_count', 'contents',
        ]
        rows = []
        async with dc_api.API() as api:
            i_post = 0
            async for index in api.board(board_id=board_id):
                if(int(index.id) <= last_id):
                    break
                if(get_contents):
                    print("({}|{}) getting doc ... ".format(board_id, index.id), end="")
                    doc = await self.get_document(int(index.id), board_id)
                    if doc is None:
                        print("skipped")
                        continue
                    index.author = doc.author
                    author_id = doc.author_id
                    contents = doc.contents
                    print("done")
                else:
                    author_id = getattr(index, "author_id", "")
                    contents = ""
                author_name, author_ip, author_id = split_author_display(
                    index.author,
                    author_id,
                )
                rows.append([
                    int(index.id), index.author, author_name, author_ip, author_id,
                    index.time, index.title, int(index.comment_count), contents,
                ])
                i_post = i_post + 1
                if(i_post == 8):
                    df = pd.DataFrame(rows, columns=cols)
                    if (self.use_cache):
                        df.to_csv('caches/' + board_id+".csv", index=False)
                    self.board.set_board(df)
                    print('done')
                    return df
        df = pd.DataFrame(rows, columns=cols)
        self.board.set_board(df)
        print('done')
        return df

    async def list_board_document_ids(self, board_id, num):
        return set(await self.list_board_document_indexes(board_id, num))

    async def list_board_document_indexes(self, board_id, num):
        indexes = {}
        async with dc_api.API() as api:
            async for index in api.board(board_id=board_id, num=num):
                indexes[int(index.id)] = index
        return indexes


    async def write_document(self,
                             board_id=None, board_minor=None,
                             name=None, title=None, contents=None):
        board_id    = self.board_id    if board_id    is None else board_id
        board_minor = self.board_minor if board_minor is None else board_minor
        name     = self.get_doc_write_name() if name is None else name
        title    = self.doc_title    if title    is None else title
        contents = self.get_doc_write_contents() if contents is None else contents
        action_type = "write_document_pc" if self.doc_write_backend == "pc" else "write_document_mobile"
        self.require_write_allowed(action_type, board_id)

        async with dc_api.API() as api:
            try:
                if self.doc_write_backend == "pc":
                    doc_id = await api.write_document_pc(
                                       board_id=board_id,
                                       name=name,
                                       password=self.password,
                                       title=title,
                                       contents=contents,
                                       use_html=self.doc_write_pc_use_html)
                else:
                    doc_id = await api.write_document(
                                       board_id=board_id,
                                       name=name,
                                       password=self.password,
                                       title=title,
                                       contents=contents,
                                       is_minor=board_minor)
            except Exception as exc:
                self.record_write_failure(action_type, board_id, exc)
                raise
        self.record_write_success(action_type, board_id)
        return doc_id

    async def remove_document(self, board_id, document_id, is_minor=False, password=None):
        password = self.password if password is None else password
        self.require_write_allowed("remove_document", board_id)
        async with dc_api.API() as api:
            try:
                result = await api.remove_document(
                    board_id=board_id,
                    document_id=document_id,
                    password=password,
                    is_minor=is_minor,
                )
            except Exception as exc:
                self.record_write_failure("remove_document", board_id, exc)
                raise
        self.record_write_success("remove_document", board_id)
        return result

    async def modify_document(self, board_id, document_id, title, contents, password=None):
        password = self.password if password is None else password
        self.require_write_allowed("modify_document_pc", board_id)
        async with dc_api.API() as api:
            try:
                result = await api.modify_document_pc(
                    board_id=board_id,
                    document_id=document_id,
                    title=title,
                    contents=contents,
                    password=password,
                )
            except Exception as exc:
                self.record_write_failure("modify_document_pc", board_id, exc)
                raise
        self.record_write_success("modify_document_pc", board_id)
        return result

    async def get_document(self, doc_id, board_id=None):
        board_id = self.board_id if board_id is None else board_id
        async with dc_api.API() as api:
            doc = await api.document(board_id=board_id, document_id=doc_id)
        return doc
    
    async def write_comment(self, doc_id,
                            name=None, contents=None):
        name     = self.nick             if name     is None else name
        contents = self.comment_contents if contents is None else contents

        self.require_write_allowed("write_comment", self.board_id)
        async with dc_api.API() as api:
            try:
                com_id = await api.write_comment \
                                   (board_id=self.board_id, document_id=doc_id,
                                    name=name, password=self.password,
                                    contents=contents)
            except Exception as exc:
                self.record_write_failure("write_comment", self.board_id, exc)
                raise
        self.record_write_success("write_comment", self.board_id)
        return com_id
    
    async def get_comments(self, board_id=None, doc_id=-1):
        board_id    = self.board_id    if board_id    is None else board_id
        cols = [
            'id', 'author', 'author_name', 'author_ip', 'author_id',
            'time', 'contents', 'is_reply',
        ]
        rows = []
        if doc_id < 0:
            return pd.DataFrame(rows, columns=cols)
        async with dc_api.API() as api:
            async for comm in api.comments(board_id=board_id, document_id=doc_id):
                author_name, author_ip, author_id = split_author_display(
                    comm.author,
                    comm.author_id,
                )
                rows.append([
                    int(comm.id), comm.author, author_name, author_ip, author_id,
                    comm.time, comm.contents, comm.is_reply,
                ])
        df = pd.DataFrame(rows, columns=cols)
        if (self.use_cache):
            df.to_csv('caches/'+board_id+".comments.csv", index=False)
        self.comments.set_comments(df)
        return df

if __name__ == "__main__":
    gallkeeper = GallKeeper("conf/default.yaml")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # 'RuntimeError: There is no current event loop...'
        loop = None
    
    if loop and loop.is_running():
        print('Async event loop already running. Adding coroutine to the event loop.')
        tsk = loop.create_task(gallkeeper.run())
        # ^-- https://docs.python.org/3/library/asyncio-task.html#task-object
        # Optionally, a callback function can be executed when the coroutine completes
        tsk.add_done_callback(
            lambda t: print(f'Task done with result={t.result()}  << return val of run()'))
        # loop.close()
    else:
        print('Starting new event loop')
        asyncio.run(gallkeeper.run())
