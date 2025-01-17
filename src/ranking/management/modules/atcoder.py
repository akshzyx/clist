# -*- coding: utf-8 -*-

import collections
import functools
import html
import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor as PoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta

import arrow
import pytz
from first import first
from ratelimiter import RateLimiter
from tqdm import tqdm

from ranking.management.modules import conf
from ranking.management.modules.common import LOG, REQ, BaseModule, FailOnGetResponse, parsed_table
from ranking.management.modules.excepts import ExceptionParseStandings


class Statistic(BaseModule):
    STANDING_URL_ = '{0.url}/standings'
    RESULTS_URL_ = '{0.url}/results'
    SUBMISSIONS_URL_ = '{0.url}/submissions'
    PROBLEM_URL_ = '{0.url}/tasks/{1}'
    HISTORY_URL_ = '{0.scheme}://{0.netloc}/users/{1}/history'
    DEFAULT_LAST_SUBMISSION_TIME = -1
    DEFAULT_LAST_PAGE = 1

    def __init__(self, **kwargs):
        super(Statistic, self).__init__(**kwargs)
        self.cid = self.key
        self._username = conf.ATCODER_HANDLE
        self._password = conf.ATCODER_PASSWORD
        self._stop = None

    def _get(self, *args, **kwargs):
        page = REQ.get(*args, **kwargs)
        form = REQ.form(limit=2, selectors=['class="form-horizontal"'])
        if form:
            form['post'].update({
                'username': self._username,
                'password': self._password,
            })
            page = REQ.get(form['url'], post=form['post'])
        return page

    def fetch_submissions(self, fuser=None, c_page=1):
        url = self.SUBMISSIONS_URL_.format(self) + f'?page={c_page}'
        if fuser:
            url += f'&f.User={fuser}'

        for attempt in range(4):
            if self._stop:
                return

            try:
                page = self._get(url)
                break
            except FailOnGetResponse:
                time.sleep(attempt)
        else:
            return

        regex = '<table[^>]*>.*?</table>'
        html_table = re.search(regex, page, re.DOTALL).group(0)
        table = parsed_table.ParsedTable(html_table, with_duplicate_colspan=True)

        pages = re.findall(r'''<a[^>]*href=["'][^"']*/submissions\?[^"']*page=([0-9]+)[^"']*["'][^>]*>[0-9]+</a>''', page)  # noqa
        n_page = max(map(int, pages))

        return url, page, table, c_page, n_page

    def _update_submissions(self, fusers, standings):
        result = standings['result']

        with PoolExecutor(max_workers=8) as executor:
            submissions = list(tqdm(executor.map(self.fetch_submissions, fusers),
                                    total=len(fusers),
                                    desc='gettings first page'))

            for fuser, page_submissions in zip(fusers, submissions):
                if page_submissions is None:
                    break
                url, page, table, _, n_page = page_submissions

                submissions_times = {}

                def process_page(url, page, table):
                    last_submission_time = 0
                    for r in table:
                        row = dict()
                        for k, v in list(r.items()):
                            if v.value == 'Detail':
                                href = first(v.column.node.xpath('.//a/@href'))
                                if href:
                                    row['url'] = urllib.parse.urljoin(url, href)
                                    row['external_solution'] = True
                            elif k == 'User':
                                row['name'] = v.value
                                href = v.column.node.xpath('.//a/@href')[0]
                                row['user'] = href.split('/')[-1]
                            else:
                                k = k.lower().replace(' ', '_')
                                row[k] = v.value

                        submission_time = arrow.get(row['submission_time'])
                        upsolve = submission_time >= self.end_time
                        row['submission_time'] = submission_time.timestamp
                        last_submission_time = max(last_submission_time, row['submission_time'])

                        row['verdict'] = row.pop('status')
                        user = row.pop('user')
                        name = row.pop('name')
                        task = row.pop('task').split()[0]
                        score = float(row.pop('score'))

                        res = result.setdefault(user, collections.OrderedDict())
                        res.setdefault('member', user)
                        if name != user:
                            res['name'] = name
                        problems = res.setdefault('problems', {})
                        problem = problems.setdefault(task, {})
                        problem_score = problem.get('result', 0)
                        eps = 1e-9
                        if upsolve:
                            problem = problem.setdefault('upsolving', {})
                            st = submissions_times.setdefault((user, task), problem.get('submission_time'))
                            problem_score = problem.get('result', 0)
                            if 'submission_time' not in problem:
                                problem_score = 0

                            if score > eps:
                                row['result'] = score
                            elif problem_score < eps and (not st or st < row['submission_time']):
                                problem['result'] = problem_score - 1
                                row.pop('result', None)

                        if 'submission_time' in problem and row['submission_time'] <= problem['submission_time']:
                            continue

                        if problem_score > eps and problem_score > score:
                            continue

                        problem.update(row)
                    return last_submission_time

                last_submission_time = process_page(url, page, table)

                st_data = result.get(fuser, {}) if fuser else standings
                submissions_info = st_data.setdefault('_submissions_info', {})
                limit_st = submissions_info.pop('last_submission_time', self.DEFAULT_LAST_SUBMISSION_TIME)
                last_page = submissions_info.pop('last_page', self.DEFAULT_LAST_PAGE)
                last_page_st = submissions_info.pop('last_page_st', self.DEFAULT_LAST_SUBMISSION_TIME)
                c_page = last_page

                self._stop = False
                fetch_submissions_user = functools.partial(self.fetch_submissions, fuser)
                for page_submissions in tqdm(
                    executor.map(fetch_submissions_user, range(last_page + 1, n_page + 1)),
                    total=n_page - last_page,
                    desc=f'getting submissions for ({last_page};{n_page}]'
                ):
                    if page_submissions is None:
                        submissions_info['last_page'] = c_page
                        submissions_info['last_page_st'] = last_page_st
                        LOG.info(f'stopped after ({last_page};{c_page}] of {n_page}')
                        self._stop = True
                        break

                    url, page, table, c_page, _ = page_submissions
                    submission_time = process_page(url, page, table)
                    last_page_st = max(last_page_st, submission_time)

                    if submission_time < limit_st:
                        self._stop = True
                        break
                if 'last_page' not in submissions_info:
                    submissions_info['last_submission_time'] = \
                        last_submission_time if last_page == self.DEFAULT_LAST_PAGE else last_page_st

    def get_standings(self, users=None, statistics=None):

        result = {}

        if users and statistics:
            fusers = users
            for member in users:
                if member not in statistics:
                    continue

                info = collections.OrderedDict(deepcopy(statistics.get(member)))
                info['member'] = member
                for f in ('place', 'solving', 'upsolving'):
                    info[f] = '__unchanged__'
                result[member] = info

            standings = {'result': result}
        else:
            fusers = []
            page = self._get(self.url)
            match = re.search(r'(?<=<li>)Writer:.*', page)
            writers = []
            if match:
                matches = re.findall('(?<=>)[^<]+(?=</)', match.group())
                writers = list()
                for m in matches:
                    writers.extend(map(str.strip, re.split(r'[,\s]+', m)))
                writers = [w for w in writers if w and w != '?']

            url = f'{self.RESULTS_URL_.format(self)}/'
            page = self._get(url)

            match = re.search(r'var\s*results\s*=\s*(\[[^\n]*\]);$', page, re.MULTILINE)
            data = json.loads(match.group(1))
            results = {}
            for row in data:
                if not row.get('IsRated'):
                    continue
                handle = row.pop('UserScreenName')
                if users and handle not in users:
                    continue
                if 'NewRating' not in row:
                    continue
                r = collections.OrderedDict()
                for k in ['OldRating', 'NewRating', 'Performance']:
                    if k in row:
                        r[k] = row[k]
                results[handle] = r

            url = f'{self.STANDING_URL_.format(self)}/json'
            try:
                page = self._get(url)
            except FailOnGetResponse as e:
                if e.code == 404:
                    return {'action': 'delete'}
                raise e
            data = json.loads(page)

            task_info = collections.OrderedDict()
            for t in data['TaskInfo']:
                k = t['TaskScreenName']
                url = self.PROBLEM_URL_.format(self, k)
                task_info[k] = {
                    'code': k,
                    'short': t['Assignment'],
                    'name': t['TaskName'],
                    'url': url,
                }

            def get_problem_full_score(info):
                page = REQ.get(info['url'])
                match = re.search('<span[^>]*class="lang-[a-z]+"[^>]*>\s*<p>\s*(?:配点|Score)\s*(?:：|:)\s*<var>\s*(?P<score>[0-9]+)\s*</var>\s*(?:点|points)\s*</p>', page, re.I)  # noqa
                if match:
                    info['full_score'] = int(match.group('score'))

            with PoolExecutor(max_workers=8) as executor:
                executor.map(get_problem_full_score, task_info.values())

            has_rated = False
            has_new_rating = False

            for row in data['StandingsData']:
                if not row['TaskResults'] and not row.get('IsRated'):
                    continue
                handle = row.pop('UserScreenName')
                if users is not None and handle not in users:
                    continue
                r = result.setdefault(handle, collections.OrderedDict())
                r['member'] = handle
                if row.pop('UserIsDeleted', None):
                    r['action'] = 'delete'
                    continue
                r['place'] = row.pop('Rank')
                total_result = row.pop('TotalResult')

                penalty = total_result['Elapsed'] // 10**9
                r['penalty'] = f'{penalty // 60}:{penalty % 60:02d}'

                r['solving'] = total_result['Score'] / 100.
                r['country'] = row.pop('Country')
                if 'UserName' in row:
                    r['name'] = row.pop('UserName')

                r['url'] = self.SUBMISSIONS_URL_.format(self) + f'?f.User={handle}'

                stats = (statistics or {}).get(handle, {})
                problems = r.setdefault('problems', stats.get('problems', {}))
                solving = 0
                task_results = row.pop('TaskResults', {})
                for k, v in task_results.items():
                    if 'Score' not in v:
                        continue
                    letter = task_info[k]['short']
                    p = problems.setdefault(letter, {})

                    if v['Score'] > 0:
                        solving += 1

                        p['result'] = v['Score'] / 100.

                        seconds = v['Elapsed'] // 10**9
                        p['time_in_seconds'] = seconds
                        p['time'] = f'{seconds // 60}:{seconds % 60:02d}'

                        if v['Penalty'] > 0:
                            p['penalty'] = v['Penalty']
                    else:
                        p['result'] = -v['Failure']
                r['solved'] = {'solving': solving}

                row.update(r)
                row.pop('Additional', None)
                if 'AtCoderRank' in row:
                    row['AtcoderRank'] = row.pop('AtCoderRank')
                rating = row.pop('Rating', None)
                if rating is not None:
                    r['info'] = {'rating': rating}
                old_rating = row.pop('OldRating', None)

                for k, v in sorted(row.items()):
                    r[k] = v

                if handle in results:
                    r.update(results.pop(handle))

                if old_rating is not None and (old_rating or 'NewRating' in r):
                    r['OldRating'] = old_rating

                if r.get('IsRated'):
                    has_rated = True
                    if r.get('NewRating') is not None:
                        has_new_rating = True

            url = f'{self.STANDING_URL_.format(self)}/virtual/json'
            page = self._get(url)
            data = json.loads(page)

            for row in data['StandingsData']:
                if not row['TaskResults']:
                    continue
                handle = row.pop('UserScreenName')
                if users is not None and handle not in users:
                    continue
                r = result.setdefault(handle, collections.OrderedDict())
                r['member'] = handle
                if row.pop('UserIsDeleted', None):
                    r['action'] = 'delete'
                    continue

                r['country'] = row.pop('Country')
                if 'UserName' in row:
                    r['name'] = row.pop('UserName')
                r['url'] = self.SUBMISSIONS_URL_.format(self) + f'?f.User={handle}'

                stats = (statistics or {}).get(handle, {})
                problems = r.setdefault('problems', stats.get('problems', {}))
                task_results = row.pop('TaskResults', {})
                for k, v in task_results.items():
                    if 'Score' not in v:
                        continue
                    letter = task_info[k]['short']
                    p = problems.setdefault(letter, {})
                    p = p.setdefault('upsolving', {})
                    p_result = p.get('result', 0)
                    score = v['Score'] / 100.
                    if score > 0 and score > p_result:
                        p['result'] = score

                        seconds = v['Elapsed'] // 10**9
                        p['time_in_seconds'] = seconds
                        p['time'] = f'{seconds // 60}:{seconds % 60:02d}'

                        if v['Penalty'] > 0:
                            p['penalty'] = v['Penalty']
                    elif score <= 0 and p_result <= 0 and -v['Failure'] < p_result:
                        p['result'] = -v['Failure']

            if self.info.get('_submissions_info', {}).get('last_submission_time', -1) > 0:
                for r in result.values():
                    problems = r.get('problems', {})
                    if not problems:
                        continue
                    no_url = False
                    for p in problems.values():
                        u = p.get('upsolving', {})
                        if 'result' in p and 'url' not in p or 'result' in u and 'url' not in u:
                            no_url = True
                            break
                    if no_url:
                        fusers.append(r['member'])

            if statistics:
                for member, row in statistics.items():
                    if member not in result:
                        has_result = any('result' in p for p in row.get('problems', {}).values())
                        if has_result:
                            continue
                        row['member'] = member
                        result[member] = row

            standings = {
                'result': result,
                'url': self.STANDING_URL_.format(self),
                'problems': list(task_info.values()),
                'writers': writers,
            }

            if (
                has_rated
                and not has_new_rating
                and self.end_time + timedelta(hours=3) > datetime.utcnow().replace(tzinfo=pytz.utc)
            ):
                standings['timing_statistic_delta'] = timedelta(minutes=30)

        if users or users is None:
            self._stop = False
            page_submissions = self.fetch_submissions()
            if page_submissions is not None:
                standings['_submissions_info'] = {} if statistics is None else self.info.pop('_submissions_info', {})
                standings['info_fields'] = ['_submissions_info']

                *_, n_page = page_submissions

                if not users:
                    if fusers:
                        LOG.info(f'Numbers of users without urls for some problems: {len(fusers)}')
                    if not fusers or 'last_page' in standings['_submissions_info']:
                        fusers = [None]
                    elif len(fusers) > n_page:
                        standings['_submissions_info'].pop('_last_submission_time', None)
                        fusers = [None]

                self._update_submissions(fusers, standings)

            if page_submissions is None or 'last_page' in standings['_submissions_info']:
                delta = timedelta(minutes=15)
                LOG.info(f'Repeat statistics update after {delta}')
                standings['timing_statistic_delta'] = delta

        for row in result.values():
            has_result = any('result' in p for p in row.get('problems', {}).values())
            if has_result or row.get('IsRated'):
                row.pop('_no_update_n_contests', None)
            else:
                row['_no_update_n_contests'] = True

        standings['hidden_fields'] = [
            'Affiliation',
            'AtcoderRank',
            'Competitions',
            'IsRated',
            'IsTeam',
        ]
        return standings

    @staticmethod
    def get_users_infos(users, resource, accounts, pbar=None):

        key_value_re = re.compile(
            r'''
            <tr>[^<]*<th[^>]*class="no-break"[^>]*>(?P<key>[^<]*)</th>[^<]*
            <td[^>]*>(?:\s*<[^>]*>)*(?P<value>[^<]+)
            ''',
            re.VERBOSE,
        )
        avatar_re = re.compile('''<img[^>]*class=["']avatar["'][^>]*src=["'](?P<url>[^"']*/icons/[^"']*)["'][^>]*>''')

        @RateLimiter(max_calls=100, period=10)
        def fetch_profile(user):
            url = resource.profile_url.format(account=user)
            try:
                page = REQ.get(url)
            except FailOnGetResponse as e:
                if isinstance(e.args[0], UnicodeEncodeError):
                    return None
                code = e.code
                if code == 404:
                    return None
                return {}
            ret = {}
            matches = key_value_re.finditer(page, re.VERBOSE)
            for match in matches:
                key = match.group('key').strip()
                value = match.group('value').strip()
                if value:
                    ret[key] = value
            match = avatar_re.search(page)
            if match:
                ret['avatar'] = match.group('url')
            if 'Rating' in ret:
                ret['rating'] = int(ret['Rating'])
            return ret

        with PoolExecutor(max_workers=8) as executor:
            profiles = executor.map(fetch_profile, users)
            for data in profiles:
                if not data:
                    if data is None:
                        yield {'info': None}
                    else:
                        yield {'skip': True}
                else:
                    yield {'info': data}
                pbar.update()

    @staticmethod
    def get_source_code(contest, problem):
        if 'url' not in problem:
            raise ExceptionParseStandings('Not found url')

        page = REQ.get(problem['url'])
        match = re.search('<pre[^>]*id="submission-code"[^>]*>(?P<source>[^<]*)</pre>', page)
        if not match:
            raise ExceptionParseStandings('Not found source code')
        solution = html.unescape(match.group('source'))
        return {'solution': solution}
