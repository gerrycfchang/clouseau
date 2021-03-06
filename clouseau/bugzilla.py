# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import six
import re
import functools
from .connection import (Connection, Query)
from . import config


class Bugzilla(Connection):
    """Connection to bugzilla.mozilla.org
    """

    URL = config.get('Bugzilla', 'URL', 'https://bugzilla.mozilla.org')
    # URL = config.get('Allizgub', 'URL', 'https://bugzilla-dev.allizom.org')
    API_URL = URL + '/rest/bug'
    TOKEN = config.get('Bugzilla', 'token', '')
    # TOKEN = config.get('Allizgub', 'token', '')

    def __init__(self, bugids=None, include_fields='_default', bughandler=None, bugdata=None, historyhandler=None, historydata=None, commenthandler=None, commentdata=None, attachmenthandler=None, attachmentdata=None, attachment_include_fields=None, queries=None, **kwargs):
        """Constructor

        Args:
            bugids (List[str]): list of bug ids or search query
            include_fields (List[str]): list of include fields
            bughandler (Optional[function]): the handler to use with each retrieved bug
            bugdata (Optional): the data to use with the bug handler
            historyhandler (Optional[function]): the handler to use with each retrieved bug history
            historydata (Optional): the data to use with the history handler
            commenthandler (Optional[function]): the handler to use with each retrieved bug comment
            commentdata (Optional): the data to use with the comment handler
            attachmenthandler (Optional[function]): the handler to use with each retrieved bug attachment
            attachmentdata (Optional): the data to use with the attachment handler
            attachment_include_fields (Optional[List[str]]): list of attachment include fields
            queries (List[Query]): queries rather than single query
        """
        if queries:
            super(Bugzilla, self).__init__(Bugzilla.URL, queries=queries, **kwargs)
        else:
            super(Bugzilla, self).__init__(Bugzilla.URL, **kwargs)
            if isinstance(bugids, six.string_types) or isinstance(bugids, dict):
                self.bugids = [bugids]
            elif isinstance(bugids, int):
                self.bugids = [str(bugids)]
            else:
                self.bugids = list(bugids)
            self.include_fields = include_fields
            self.bughandler = bughandler
            self.bugdata = bugdata
            self.historyhandler = historyhandler
            self.historydata = historydata
            self.commenthandler = commenthandler
            self.commentdata = commentdata
            self.attachmenthandler = attachmenthandler
            self.attachmentdata = attachmentdata
            self.attachment_include_fields = attachment_include_fields
            self.bugs_results = []
            self.history_results = []
            self.comment_results = []
            self.attachment_results = []
            self.got_data = False

    def get_header(self):
        header = super(Bugzilla, self).get_header()
        header['X-Bugzilla-API-Key'] = self.get_apikey()
        return header

    def put(self, data):
        """Put some data in bugs

        Args:
            data (dict): a dictionnary
        """
        if self.bugids:
            if self.__is_bugid():
                ids = self.bugids
            else:
                ids = self.__get_bugs_list()

            url = Bugzilla.API_URL + '/'
            failed = ids
            header = self.get_header()

            def cb(data, sess, res):
                if res.status_code == 200:
                    json = res.json()
                    if json.get('error', False):
                        failed.extend(data)

            while failed:
                _failed = list(failed)
                failed = []
                for _ids in Connection.chunks(_failed):
                    first_id = _ids[0]
                    if len(_ids) >= 2:
                        data['ids'] = _ids
                    elif 'ids' in data:
                        del data['ids']
                    self.session.put(url + first_id,
                                     json=data,
                                     headers=header,
                                     verify=True,
                                     timeout=self.TIMEOUT,
                                     background_callback=functools.partial(cb, _ids)).result()

    def get_data(self):
        """Collect the data
        """
        if not self.got_data:
            self.got_data = True
            if self.__is_bugid():
                if self.bughandler:
                    self.__get_bugs()
                if self.historyhandler:
                    self.__get_history()
                if self.commenthandler:
                    self.__get_comment()
                if self.attachmenthandler:
                    self.__get_attachment()
            elif self.bughandler:
                self.__get_bugs_for_history_comment()

        return self

    def wait(self):
        if self.queries:
            super(Bugzilla, self).wait()
        else:
            self.get_data()
            self.wait_bugs()
            for r in self.comment_results:
                r.result()
            for r in self.history_results:
                r.result()
            for r in self.attachment_results:
                r.result()

    def wait_bugs(self):
        """Just wait for bugs
        """
        for r in self.bugs_results:
            r.result()

    @staticmethod
    def get_links(bugids):
        if isinstance(bugids, six.string_types):
            return 'https://bugzil.la/' + bugids
        else:
            return ['https://bugzil.la/' + str(bugid) for bugid in bugids]

    @staticmethod
    def follow_dup(bugids, only_final=True):
        """Follow the duplicated bugs

        Args:
            bugids (List[str]): list of bug ids
            only_final (bool): if True only the final bug is returned else all the chain

        Returns:
            dict: each bug in entry is mapped to the last bug in the duplicate chain (None if there's no dup and 'cycle' if a cycle is detected)
        """
        include_fields = ['id', 'resolution', 'dupe_of']
        dup = {}
        _set = set()
        for bugid in bugids:
            dup[str(bugid)] = None

        def bughandler(bug, data):
            if bug['resolution'] == 'DUPLICATE':
                dupeofid = str(bug['dupe_of'])
                dup[str(bug['id'])] = [dupeofid]
                _set.add(dupeofid)

        bz = Bugzilla(bugids=bugids, include_fields=include_fields, bughandler=bughandler).get_data()
        bz.wait_bugs()

        def bughandler2(bug, data):
            if bug['resolution'] == 'DUPLICATE':
                bugid = str(bug['id'])
                for _id, dupid in dup.items():
                    if dupid and dupid[-1] == bugid:
                        dupeofid = str(bug['dupe_of'])
                        if dupeofid == _id or dupeofid in dupid:
                            # avoid infinite loop if any
                            dup[_id].append('cycle')
                        else:
                            dup[_id].append(dupeofid)
                            _set.add(dupeofid)

        bz.bughandler = bughandler2

        while _set:
            bz.bugids = list(_set)
            _set.clear()
            bz.got_data = False
            bz.get_data().wait_bugs()

        if only_final:
            for k, v in dup.items():
                dup[k] = v[-1] if v else None

        return dup

    @staticmethod
    def get_history_matches(history, change_to_match):
        history_entries = []

        for history_entry in history:
            for change in history_entry['changes']:
                matches = True

                for change_key, change_value in change.items():
                    for key, value in change_to_match.items():
                        if key == change_key and value != change_value and value not in change_value.split(', '):
                            matches = False
                            break

                    if not matches:
                        break

                if matches:
                    history_entries.append(history_entry)
                    break

        return history_entries

    @staticmethod
    def get_landing_patterns(channels=['release', 'beta', 'aurora', 'nightly']):
        if not isinstance(channels, list):
            channels = [channels]

        landing_patterns = []
        for channel in channels:
            if channel in ['central', 'nightly']:
                landing_patterns += [
                    (re.compile('://hg.mozilla.org/mozilla-central/rev/([0-9a-z]+)'), channel),
                    (re.compile('://hg.mozilla.org/mozilla-central/pushloghtml\?changeset=([0-9a-z]+)'), channel),
                ]
            elif channel == 'inbound':
                landing_patterns += [(re.compile('://hg.mozilla.org/integration/mozilla-inbound/rev/([0-9a-z]+)'), 'inbound')]
            elif channel in ['release', 'beta', 'aurora']:
                landing_patterns += [(re.compile('://hg.mozilla.org/releases/mozilla-' + channel + '/rev/([0-9a-z]+)'), channel)]
            elif channel == 'fx-team':
                landing_patterns += [(re.compile('://hg.mozilla.org/integration/fx-team/rev/([0-9a-z]+)'), 'inbound')]
            else:
                raise Exception('Unexpected channel: ' + channel)

        return landing_patterns

    @staticmethod
    def get_landing_comments(comments, channels, landing_patterns=None):
        if not landing_patterns:
            landing_patterns = Bugzilla.get_landing_patterns(channels)

        results = []

        for comment in comments:
            for landing_pattern in landing_patterns:
                for match in landing_pattern[0].finditer(comment['text']):
                    results.append({
                        'comment': comment,
                        'revision': match.group(1),
                        'channel': landing_pattern[1],
                    })

        return results

    @staticmethod
    def remove_private_bugs(bugids):
        """Remove private bugs from the list

        Args:
            bugids (list): list of bug ids

        Returns:
            (list): list of accessible bugs
        """
        def bughandler(bug, data):
            data.append(str(bug['id']))

        data = []
        Bugzilla(bugids, include_fields=['id'], bughandler=bughandler, bugdata=data).wait()

        return data

    def __is_bugid(self):
        """Check if the first bugid is a bug id or a search query

        Returns:
            (bool): True if the first bugid is a bug id
        """
        if self.bugids:
            bugid = self.bugids[0]
            if not isinstance(bugid, dict) and str(bugid).isdigit():
                return True
        return False

    def __get_bugs_for_history_comment(self):
        """Get history and comment (if there are some handlers) after a search query
        """
        if self.historyhandler or self.commenthandler or self.attachmenthandler:
            bugids = []
            bughandler = self.bughandler
            bugdata = self.bugdata

            def __handler(bug, bd):
                bughandler(bug, bugdata)
                bd.append(bug['id'])

            self.bughandler = __handler
            self.bugdata = bugids

            self.__get_bugs_by_search()
            self.wait_bugs()

            self.bughandler = bughandler
            self.bugdata = bugdata

            self.bugids = bugids

            if self.historyhandler:
                self.history_results = []
                self.__get_history()
            if self.commenthandler:
                self.comment_results = []
                self.__get_comment()
            if self.attachmenthandler:
                self.attachment_results = []
                self.__get_attachment()
        else:
            self.__get_bugs_by_search()

    def __bugs_cb(self, sess, res):
        """Callback for bug query

        Args:
            sess: session
            res: result
        """
        if res.status_code == 200:
            for bug in res.json()['bugs']:
                self.bughandler(bug, self.bugdata)

    def __get_bugs(self):
        """Get the bugs
        """
        header = self.get_header()
        for bugids in Connection.chunks(self.bugids):
            self.bugs_results.append(self.session.get(Bugzilla.API_URL,
                                                      params={'id': ','.join(map(str, bugids)),
                                                              'include_fields': self.include_fields},
                                                      headers=header,
                                                      verify=True,
                                                      timeout=self.TIMEOUT,
                                                      background_callback=self.__bugs_cb))

    def __get_bugs_by_search(self):
        """Get the bugs in making a search query
        """
        url = Bugzilla.API_URL + '?'
        header = self.get_header()
        for query in self.bugids:
            if isinstance(query, six.string_types):
                url = Bugzilla.API_URL + '?' + query
                params = None
            else:
                url = Bugzilla.API_URL
                params = query

            self.bugs_results.append(self.session.get(url,
                                                      params=params,
                                                      headers=header,
                                                      verify=True,
                                                      timeout=self.TIMEOUT,
                                                      background_callback=self.__bugs_cb))

    def __get_bugs_list(self):
        """Get the bugs list corresponding to the search query
        """
        _list = set()

        def cb(sess, res):
            if res.status_code == 200:
                for bug in res.json()['bugs']:
                    _list.add(bug['id'])

        results = []
        url = Bugzilla.API_URL + '?'
        header = self.get_header()
        for query in self.bugids:
            results.append(self.session.get(url + query,
                                            headers=header,
                                            verify=True,
                                            timeout=self.TIMEOUT,
                                            background_callback=cb))

        for r in results():
            r.result()

        return list(_list)

    def __history_cb(self, sess, res):
        """Callback for bug history

        Args:
            sess: session
            res: result
        """
        if res.status_code == 200:
            json = res.json()
            if 'bugs' in json and json['bugs']:
                for h in json['bugs']:
                    self.historyhandler(h, self.historydata)

    def __get_history(self):
        """Get the bug history
        """
        url = Bugzilla.API_URL + '/%s/history'
        header = self.get_header()
        # TODO: remove next line after the fix of bug 1283392
        bugids = Bugzilla.remove_private_bugs(self.bugids)
        for _bugids in Connection.chunks(bugids):
            first = _bugids[0]
            remainder = _bugids[1:] if len(_bugids) >= 2 else []
            self.history_results.append(self.session.get(url % first,
                                                         headers=header,
                                                         params={'ids': remainder},
                                                         verify=True,
                                                         timeout=self.TIMEOUT,
                                                         background_callback=self.__history_cb))

    def __comment_cb(self, sess, res):
        """Callback for bug comment

        Args:
            sess: session
            res: result
        """
        if res.status_code == 200:
            json = res.json()
            if 'bugs' in json:
                bugs = json['bugs']
                if bugs:
                    for key in bugs.keys():
                        if isinstance(key, six.string_types) and key.isdigit():
                            comments = bugs[key]
                            self.commenthandler(comments, key, self.commentdata)

    def __get_comment(self):
        """Get the bug comment
        """
        url = Bugzilla.API_URL + '/%s/comment'
        header = self.get_header()
        # TODO: remove next line after the fix of bug 1283392
        bugids = Bugzilla.remove_private_bugs(self.bugids)
        for _bugids in Connection.chunks(bugids):
            first = _bugids[0]
            remainder = _bugids[1:] if len(_bugids) >= 2 else []
            self.comment_results.append(self.session.get(url % first,
                                                         headers=header,
                                                         params={'ids': remainder},
                                                         verify=True,
                                                         timeout=self.TIMEOUT,
                                                         background_callback=self.__comment_cb))

    def __attachment_cb(self, sess, res):
        """Callback for bug attachment

        Args:
            sess: session
            res: result
        """
        if res.status_code == 200:
            json = res.json()
            if 'bugs' in json:
                bugs = json['bugs']
                if bugs:
                    for key in bugs.keys():
                        if isinstance(key, six.string_types) and key.isdigit():
                            attachments = bugs[key]
                            self.attachmenthandler(attachments, key, self.attachmentdata)
                            break

    def __get_attachment(self):
        """Get the bug attachment
        """
        url = Bugzilla.API_URL + '/%s/attachment'
        header = self.get_header()
        req_params = {'include_fields': self.attachment_include_fields}
        for bugid in self.bugids:
            self.attachment_results.append(self.session.get(url % bugid,
                                                            headers=header,
                                                            params=req_params,
                                                            verify=True,
                                                            timeout=self.TIMEOUT,
                                                            background_callback=self.__attachment_cb))


class BugzillaUser(Connection):
    """Connection to bugzilla.mozilla.org
    """

    URL = config.get('Bugzilla', 'URL', 'https://bugzilla.mozilla.org')
    API_URL = URL + '/rest/user'
    TOKEN = config.get('Bugzilla', 'token', '')

    def __init__(self, user_names=None, search_strings=None, include_fields='_default', user_handler=None, user_data=None, **kwargs):
        """Constructor

        Args:
            user_names (List[str]): list of user names or IDs
            search_strings (List[str]): list of search strings
            include_fields (List[str]): list of include fields
            user_handler (Optional[function]): the handler to use with each retrieved user
            user_data (Optional): the data to use with the user handler
        """
        self.user_handler = user_handler
        self.user_data = user_data

        if user_names is not None:
            if isinstance(user_names, six.string_types) or isinstance(user_names, int):
                user_names = [user_names]

            params = {
                'include_fields': include_fields,
                'names': [user_name for user_name in user_names if isinstance(user_name, six.string_types) and not user_name.isdigit()],
                'ids': [str(user_id) for user_id in user_names if isinstance(user_id, int) or user_id.isdigit()],
            }

            super(BugzillaUser, self).__init__(BugzillaUser.URL, Query(BugzillaUser.API_URL, params, self.__users_cb), **kwargs)
        elif search_strings is not None:
            if isinstance(search_strings, six.string_types):
                search_strings = [search_strings]

            queries = []
            for search_string in search_strings:
                queries.append(Query(BugzillaUser.API_URL + '?' + search_string, handler=self.__users_cb))

            super(BugzillaUser, self).__init__(BugzillaUser.URL, queries, **kwargs)

    def get_header(self):
        header = super(BugzillaUser, self).get_header()
        header['X-Bugzilla-API-Key'] = self.get_apikey()
        return header

    def __users_cb(self, res):
        if not self.user_handler:
            return

        for user in res['users']:
            if self.user_data is not None:
                self.user_handler(user, self.user_data)
            else:
                self.user_handler(user)
