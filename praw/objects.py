# This file is part of PRAW.
#
# PRAW is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# PRAW is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# PRAW.  If not, see <http://www.gnu.org/licenses/>.

"""
Contains code about objects such as Submissions, Deletable or Commments

There are two main groups of objects in this file. The first are objects that
correspond to a Thing or part of a Thing as specified in reddit's API overview,
https://github.com/reddit/reddit/wiki/API. The second gives functionality that
extends over multiple Things. An object that extends from Saveable indicates
that it can be saved and unsaved in the context of a logged in user.
"""

import six
import warnings

from requests.compat import urljoin
from praw.decorators import limit_chars, require_login
from praw.errors import ClientException
from praw.helpers import (_get_section, _get_sorter, _modify_relationship,
                          _request)


REDDITOR_KEYS = ('approved_by', 'author', 'banned_by', 'redditor')


class RedditContentObject(object):
    """Base class that represents actual reddit objects."""
    @classmethod
    def from_api_response(cls, reddit_session, json_dict):
        return cls(reddit_session, json_dict=json_dict)

    def __init__(self, reddit_session, json_dict=None, fetch=True,
                 info_url=None, underscore_names=None):
        """
        Create a new object from the dict of attributes returned by the API.

        The fetch parameter specifies whether to retrieve the object's
        information from the API (only matters when it isn't provided using
        json_dict).
        """
        if info_url:
            self._info_url = info_url
        else:
            self._info_url = reddit_session.config['info']
        self.reddit_session = reddit_session
        self._underscore_names = underscore_names
        self._populated = self._populate(json_dict, fetch)

    def __eq__(self, other):
        return (isinstance(other, RedditContentObject) and
                self.content_id == other.content_id)

    def __getattr__(self, attr):
        if not self._populated:
            self._populated = self._populate(None, True)
            return getattr(self, attr)
        raise AttributeError('\'%s\' has no attribute \'%s\'' % (type(self),
                                                                 attr))

    def __ne__(self, other):
        return not (self == other)

    def __setattr__(self, name, value):
        if value and name == 'subreddit':
            value = Subreddit(self.reddit_session, value, fetch=False)
        elif value and name in REDDITOR_KEYS:
            if isinstance(value, bool):
                pass
            elif not value or value == '[deleted]':
                value = None
            else:
                value = Redditor(self.reddit_session, value, fetch=False)
        object.__setattr__(self, name, value)

    def __str__(self):
        retval = self.__unicode__()
        if not six.PY3:
            retval = retval.encode('utf-8')
        return retval

    def _get_json_dict(self):
        response = self.reddit_session.request_json(self._info_url,
                                                    as_objects=False)
        return response['data']

    def _populate(self, json_dict, fetch):
        if json_dict is None:
            if fetch:
                json_dict = self._get_json_dict()
            else:
                json_dict = {}
        for name, value in six.iteritems(json_dict):
            if self._underscore_names and name in self._underscore_names:
                name = '_' + name
            setattr(self, name, value)
        return bool(json_dict) or fetch

    @property
    def content_id(self):
        """
        Get the object's content id.

        An object id is its object to kind mapping like t3 followed by an
        underscore and then its id. E.g. 't1_c5s96e0'.
        """
        by_object = self.reddit_session.config.by_object
        return '%s_%s' % (by_object[self.__class__], self.id)


class Approvable(RedditContentObject):
    """Interface for Reddit content objects that can be approved."""
    @require_login
    def approve(self):
        """Give approval to object."""
        url = self.reddit_session.config['approve']
        params = {'id': self.content_id}
        response = self.reddit_session.request_json(url, params)
        urls = [self.reddit_session.config[x] for x in ['modqueue', 'spam']]
        _request.evict(urls)  # pylint: disable-msg=E1101
        return response

    @require_login
    def remove(self, spam=False):
        """Remove approval from object."""
        url = self.reddit_session.config['remove']
        params = {'id': self.content_id,
                  'spam': 'True' if spam else 'False'}
        response = self.reddit_session.request_json(url, params)
        urls = [self.reddit_session.config[x] for x in ['modqueue', 'spam']]
        _request.evict(urls)  # pylint: disable-msg=E1101
        return response


class Deletable(RedditContentObject):
    """Interface for Reddit content objects that can be deleted."""
    def delete(self):
        """Delete this object."""
        url = self.reddit_session.config['del']
        params = {'id': self.content_id}
        response = self.reddit_session.request_json(url, params)
        # pylint: disable-msg=E1101
        _request.evict([self.reddit_session.config['user']])
        return response


class Distinguishable(RedditContentObject):
    """Interface for Reddit content objects that can be distinguished."""
    @require_login
    def distinguish(self):
        """Distinguish object as made by mod / admin."""
        url = self.reddit_session.config['distinguish']
        params = {'id': self.content_id}
        return self.reddit_session.request_json(url, params)

    @require_login
    def undistinguish(self):
        """Remove mod / admin distinguishing on object."""
        url = self.reddit_session.config['undistinguish']
        params = {'id': self.content_id}
        return self.reddit_session.request_json(url, params)


class Editable(RedditContentObject):
    """Interface for Reddit content objects that can be edited."""
    def edit(self, text):
        """Edit the object to `text`"""
        url = self.reddit_session.config['edit']
        params = {'thing_id': self.content_id,
                  'text': text}
        response = self.reddit_session.request_json(url, params)
        # pylint: disable-msg=E1101
        _request.evict([self.reddit_session.config['user']])
        # REDDIT: reddit's end should only ever return a single comment
        return response['data']['things'][0]


class Hideable(RedditContentObject):
    """Interface for objects that can be hidden."""
    @require_login
    def hide(self, unhide=False):
        """Hide object in the context of the logged in user."""
        url = self.reddit_session.config['unhide' if unhide else 'hide']
        params = {'id': self.content_id,
                  'executed': 'unhide' if unhide else 'hide'}
        response = self.reddit_session.request_json(url, params)
        # pylint: disable-msg=W0212
        urls = [urljoin(self.reddit_session.user._url, 'hidden')]
        _request.evict(urls)
        return response

    def unhide(self):
        """Unhide object in the context of the logged in user."""
        return self.hide(unhide=True)


class Inboxable(RedditContentObject):
    """Interface for Reddit content objects that appear in the Inbox."""
    def mark_as_read(self):
        """Mark object as read."""
        return self.reddit_session.user.mark_as_read(self)

    def mark_as_unread(self):
        """Mark object as unread."""
        return self.reddit_session.user.mark_as_read(self, unread=True)

    def reply(self, text):
        """Reply to object with the specified text."""
        # pylint: disable-msg=E1101,W0212
        response = self.reddit_session._add_comment(self.content_id, text)
        if isinstance(self, Comment):
            _request.evict([self.reddit_session.config['inbox'],
                               self.submission.permalink])
        elif isinstance(self, Message):
            _request.evict([self.reddit_session.config['inbox'],
                               self.reddit_session.config['sent']])
        return response


class Messageable(RedditContentObject):
    """Interface for RedditContentObjects that can be messaged."""
    def compose_message(self, subject, message):
        """A deprecated alias for send_message. Do not use."""
        # Remove around the end of 2012
        warnings.warn('compose_message has been renamed to send_message and '
                      'will be removed in a future version. Please update.',
                      DeprecationWarning)
        return self.reddit_session.send_message(self, subject, message)

    def send_message(self, subject, message):
        """Send message to subject."""
        return self.reddit_session.send_message(self, subject, message)


class NSFWable(RedditContentObject):
    """Interface for objects that can be marked as NSFW."""
    @require_login
    def mark_as_nsfw(self, unmarknsfw=False):
        """Mark object as Not Safe For Work ( Porn / Gore )."""
        url = self.reddit_session.config['unmarknsfw' if unmarknsfw else
                                         'marknsfw']
        params = {'id': self.content_id,
                  'executed': 'unmarknsfw' if unmarknsfw else 'marknsfw'}
        return self.reddit_session.request_json(url, params)

    def unmark_as_nsfw(self):
        """Mark object as Safe For Work, no porn or gore."""
        return self.mark_as_nsfw(unmarknsfw=True)


class Refreshable(RedditContentObject):
    """Interface for objects that can be refreshed."""
    def refresh(self):
        """
        Requery to update object with latest values.

        Note that if this call is made within cache_timeout as specified in
        praw.ini then this will return the cached content. Any listing, such
        as the submissions on a subreddits top page, will automatically be
        refreshed serverside. Refreshing a submission will also refresh all its
        comments.
        """
        if isinstance(self, Redditor):
            other = Redditor(self.reddit_session, self.name)
        elif isinstance(self, Submission):
            other = Submission.get_info(self.reddit_session, self.permalink)
        elif isinstance(self, Subreddit):
            other = Subreddit(self.reddit_session, self.display_name)
        self.__dict__ = other.__dict__  # pylint: disable-msg=W0201


class Reportable(RedditContentObject):
    """Interface for RedditContentObjects that can be reported."""
    @require_login
    def report(self):
        """Report this object to the moderators."""
        url = self.reddit_session.config['report']
        params = {'id': self.content_id}
        response = self.reddit_session.request_json(url, params)
        # Reported objects are automatically hidden as well
        # pylint: disable-msg=E1101
        _request.evict([self.reddit_session.config['user'],
                        urljoin(self.reddit_session.user._url, 'hidden')])
        return response


class Saveable(RedditContentObject):
    """Interface for RedditContentObjects that can be saved."""
    @require_login
    def save(self, unsave=False):
        """Save the object."""
        url = self.reddit_session.config['unsave' if unsave else 'save']
        params = {'id': self.content_id,
                  'executed': 'unsaved' if unsave else 'saved'}
        response = self.reddit_session.request_json(url, params)
        # pylint: disable-msg=E1101
        _request.evict([self.reddit_session.config['saved']])
        return response

    def unsave(self):
        """Unsave the object."""
        return self.save(unsave=True)


class Voteable(RedditContentObject):
    """Interface for RedditContentObjects that can be voted on."""
    def clear_vote(self):
        """
        Remove the logged in user's vote on the object.

        Running this on an object with no existing vote has no adverse effects.
        """
        return self.vote()

    def downvote(self):
        """Downvote object. If there already is a vote, replace it."""
        return self.vote(direction=-1)

    def upvote(self):
        """Upvote object. If there already is a vote, replace it."""
        return self.vote(direction=1)

    @require_login
    def vote(self, direction=0):
        """Vote for the given item in the direction specified."""
        url = self.reddit_session.config['vote']
        params = {'id': self.content_id,
                  'dir': six.text_type(direction)}
        # pylint: disable-msg=W0212
        urls = [urljoin(self.reddit_session.user._url, 'disliked'),
                urljoin(self.reddit_session.user._url, 'liked')]
        _request.evict(urls)
        return self.reddit_session.request_json(url, params)


class Comment(Approvable, Deletable, Distinguishable, Editable, Inboxable,
              Reportable, Voteable):
    """A class for comments."""
    def __init__(self, reddit_session, json_dict):
        super(Comment, self).__init__(reddit_session, json_dict,
                                      underscore_names=['replies'])
        if self._replies:
            self._replies = self._replies['data']['children']
        elif self._replies == '':  # Comment tree was built and there are none
            self._replies = []
        else:
            self._replies = None
        self._submission = None

    @limit_chars()
    def __unicode__(self):
        return getattr(self, 'body', '[Unloaded Comment]')

    def _update_submission(self, submission):
        """Submission isn't set on __init__ thus we need to update it."""
        # pylint: disable-msg=W0212
        submission._comments_by_id[self.name] = self
        self._submission = submission
        for reply in self._replies:
            reply._update_submission(submission)

    @property
    def is_root(self):
        """Indicate if the comment is a top level comment."""
        return (self.parent_id is None or
                self.parent_id == self.submission.content_id)

    @property
    def permalink(self):
        """Return a permalink to the comment."""
        return urljoin(self.submission.permalink, self.id)

    @property
    def replies(self):
        """Return a list of the comment replies to this comment."""
        if self._replies is None:
            response = self.reddit_session.request_json(self.permalink)
            # pylint: disable-msg=W0212
            self._replies = response[1]['data']['children'][0]._replies
        return self._replies

    @property
    def score(self):
        """Return the comment's score."""
        return self.ups - self.downs

    @property
    def submission(self):
        """Return the submission object this comment belongs to."""
        if not self._submission:  # Comment not from submission
            if hasattr(self, 'link_id'):  # from user comments page
                sid = self.link_id.split('_')[1]
            else:  # from user inbox
                sid = self.context.split('/')[4]
            self._submission = self.reddit_session.get_submission(None, sid)
        return self._submission


class Message(Inboxable):
    """A class for reddit messages (orangereds)."""
    def __init__(self, reddit_session, json_dict):
        super(Message, self).__init__(reddit_session, json_dict)
        if self.replies:
            self.replies = self.replies['data']['children']
        else:
            self.replies = []

    @limit_chars()
    def __unicode__(self):
        return 'From: %s\nSubject: %s\n\n%s' % (self.author, self.subject,
                                                self.body)


class MoreComments(RedditContentObject):
    """A class indicating there are more comments."""
    def __init__(self, reddit_session, json_dict):
        super(MoreComments, self).__init__(reddit_session, json_dict)
        self.submission = None
        self._comments = None

    def __unicode__(self):
        return '[More Comments: %d]' % self.count

    def _update_submission(self, submission):
        self.submission = submission

    def comments(self, update=True):
        """Fetch the comments for a single MoreComments object."""
        if not self._comments:
            # pylint: disable-msg=W0212
            children = [x for x in self.children if 't1_%s' % x
                        not in self.submission._comments_by_id]
            if not children:
                return None
            params = {'children': ','.join(children),
                      'link_id': self.submission.content_id,
                      'r': str(self.submission.subreddit)}
            if self.reddit_session.config.comment_sort:
                params['where'] = self.reddit_session.config.comment_sort
            url = self.reddit_session.config['morechildren']
            response = self.reddit_session.request_json(url, params)
            self._comments = response['data']['things']
            if update:
                for comment in self._comments:
                    # pylint: disable-msg=W0212
                    comment._update_submission(self.submission)
        return self._comments


class Redditor(Messageable, Refreshable):
    """A class representing the users of reddit."""
    get_overview = _get_section('')
    get_comments = _get_section('comments')
    get_submitted = _get_section('submitted')

    def __init__(self, reddit_session, user_name=None, json_dict=None,
                 fetch=True):
        info_url = reddit_session.config['user_about'] % user_name
        super(Redditor, self).__init__(reddit_session, json_dict,
                                       fetch, info_url)
        self.name = user_name
        self._url = reddit_session.config['user'] % user_name
        self._mod_subs = None

    def __repr__(self):
        """Include the user's name."""
        return 'Redditor(user_name=\'{0}\')'.format(self.name)

    def __unicode__(self):
        """Display the user's name."""
        return self.name

    @require_login
    def friend(self):
        """Friend the user."""
        return _modify_relationship('friend')(self.reddit_session.user, self)

    @require_login
    def mark_as_read(self, messages, unread=False):
        """Mark message(s) as read or unread."""
        ids = []
        if isinstance(messages, Inboxable):
            ids.append(messages.content_id)
        elif hasattr(messages, '__iter__'):
            for msg in messages:
                if not isinstance(msg, Inboxable):
                    raise ClientException('Invalid message type: %s'
                                          % type(msg))
                ids.append(msg.content_id)
        else:
            raise ClientException('Invalid message type: %s' % type(messages))
        # pylint: disable-msg=W0212
        return self.reddit_session._mark_as_read(ids, unread=unread)

    @require_login
    def unfriend(self):
        """Unfriend the user."""
        return _modify_relationship('friend', unlink=True)(
            self.reddit_session.user, self)


class LoggedInRedditor(Redditor):
    """A class for a currently logged in Redditor"""
    get_disliked = _get_section('disliked')
    get_hidden = _get_section('hidden')
    get_liked = _get_section('liked')
    get_saved = _get_section('saved')

    @require_login
    def get_inbox(self, limit=0):
        """Return a generator for inbox messages."""
        url = self.reddit_session.config['inbox']
        return self.reddit_session.get_content(url, limit=limit)

    @require_login
    def get_modmail(self, limit=0):
        """Return a generator for moderator messages."""
        url = self.reddit_session.config['moderator']
        return self.reddit_session.get_content(url, limit=limit)

    @require_login
    def get_sent(self, limit=0):
        """Return a generator for sent messages."""
        url = self.reddit_session.config['sent']
        return self.reddit_session.get_content(url, limit=limit)

    @require_login
    def get_unread(self, limit=0):
        """Return a generator for unread messages."""
        url = self.reddit_session.config['unread']
        return self.reddit_session.get_content(url, limit=limit)

    @require_login
    def my_contributions(self, limit=0):
        """Return the subreddits where the logged in user is a contributor."""
        url = self.reddit_session.config['my_con_reddits']
        return self.reddit_session.get_content(url, limit=limit)

    @require_login
    def my_moderation(self, limit=0):
        """Return the subreddits where the logged in user is a mod."""
        url = self.reddit_session.config['my_mod_reddits']
        return self.reddit_session.get_content(url, limit=limit)

    @require_login
    def my_reddits(self, limit=0):
        """Return the subreddits that the logged in user is subscribed to."""
        url = self.reddit_session.config['my_reddits']
        return self.reddit_session.get_content(url, limit=limit)


class Submission(Approvable, Deletable, Distinguishable, Editable, Hideable,
                 NSFWable, Refreshable, Reportable, Saveable, Voteable):
    """A class for submissions to reddit."""
    @staticmethod
    def get_info(reddit_session, url, comments_only=False):
        url_data = {}
        comment_limit = reddit_session.config.comment_limit
        comment_sort = reddit_session.config.comment_sort

        if reddit_session.user and reddit_session.user.is_gold:
            class_max = reddit_session.config.gold_comments_max
        else:
            class_max = reddit_session.config.regular_comments_max

        if comment_limit < 0:  # Use max for user class
            comment_limit = class_max
        elif comment_limit == 0:  # Use default
            comment_limit = None
        else:  # Use specified value
            if comment_limit > class_max:
                warnings.warn_explicit('comment_limit %d is too high (max: %d)'
                                       % (comment_limit, class_max),
                                       UserWarning, '', 0)
                comment_limit = class_max

        if comment_limit:
            url_data['limit'] = comment_limit
        if comment_sort:
            url_data['sort'] = comment_sort
        s_info, c_info = reddit_session.request_json(url, url_data=url_data)
        if comments_only:
            return c_info['data']['children']
        submission = s_info['data']['children'][0]
        submission.comments = c_info['data']['children']
        return submission

    def __init__(self, reddit_session, json_dict):
        super(Submission, self).__init__(reddit_session, json_dict)
        self.permalink = urljoin(reddit_session.config['reddit_url'],
                                 self.permalink)
        self._comments_by_id = {}
        self._all_comments = False
        self._comments = None
        self._comments_flat = None
        self._orphaned = {}

    @limit_chars()
    def __unicode__(self):
        title = self.title.replace('\r\n', ' ')
        return six.text_type('{0} :: {1}').format(self.score, title)

    def _insert_comment(self, comment):
        if comment.name in self._comments_by_id:  # Skip existing comments
            return

        comment._update_submission(self)  # pylint: disable-msg=W0212

        if comment.name in self._orphaned:  # Reunite children with parent
            comment.replies.extend(self._orphaned[comment.name])
            del self._orphaned[comment.name]

        if comment.is_root:
            self._comments.append(comment)
        elif comment.parent_id in self._comments_by_id:
            self._comments_by_id[comment.parent_id].replies.append(comment)
        else:  # Orphan
            if comment.parent_id in self._orphaned:
                self._orphaned[comment.parent_id].append(comment)
            else:
                self._orphaned[comment.parent_id] = [comment]

    def _replace_more_comments(self):
        """Replace MoreComments objects with the actual comments."""
        queue = [(None, x) for x in self.comments]
        remaining = self.reddit_session.config.more_comments_max
        if remaining < 0:
            remaining = None
        skipped = []

        while len(queue) > 0:
            parent, comm = queue.pop(0)
            if isinstance(comm, MoreComments):
                if parent:
                    parent.replies.remove(comm)
                elif parent is None:
                    self._comments.remove(comm)

                # Skip after reaching the limit
                if remaining is not None and remaining <= 0:
                    skipped.append(comm)
                    continue

                new_comments = comm.comments(update=False)

                # Don't count if no request was made
                if new_comments is None:
                    continue
                elif remaining is not None:
                    remaining -= 1

                for comment in new_comments:
                    if isinstance(comment, MoreComments):
                        # pylint: disable-msg=W0212
                        comment._update_submission(self)
                        queue.insert(0, (0, comment))
                    else:
                        # pylint: disable-msg=W0212
                        assert not comment._replies
                        # Replies needs to be an empty list
                        comment._replies = []
                        self._insert_comment(comment)
            else:
                for item in comm.replies:
                    queue.append((comm, item))

        if skipped:
            warnings.warn_explicit('Skipped %d more comments objects on %r' %
                                   (len(skipped), six.text_type(self)),
                                   UserWarning, '', 0)

    def _update_comments(self, comments):
        self._comments = comments
        for comment in self._comments:
            comment._update_submission(self)  # pylint: disable-msg=W0212

    def add_comment(self, text):
        """If logged in, comment on the submission using the specified text."""
        # pylint: disable-msg=E1101, W0212
        response = self.reddit_session._add_comment(self.content_id, text)
        _request.evict([self.permalink])
        return response

    @property
    def all_comments(self):
        """
        Return forest of all comments, with top-level comments as tree roots

        Use a comment's replies to walk down the tree. To get an unnested,
        flat list if comments use all_comments_flat. Multiple API
        requests may be needed to get all comments.
        """
        if not self._all_comments:
            self._replace_more_comments()
            self._all_comments = True
            self._comments_flat = None
        return self._comments

    @property
    def all_comments_flat(self):
        """
        Return all comments in an unnested, flat list.

        Multiple API requests may be needed to get all comments.
        """
        if not self._all_comments:
            self.all_comments  # pylint: disable-msg=W0104
        return self.comments_flat

    @property
    def comments(self):  # pylint: disable-msg=E0202
        """
        Return forest of comments, with top-level comments as tree roots.

        May contain instances of MoreComment objects. To easily replace
        these objects with Comment objects, use the all_comments property
        instead. Use comment's replies to walk down the tree. To get
        an unnested, flat list of comments use comments_flat.
        """
        if self._comments is None:
            self.comments = Submission.get_info(self.reddit_session,
                                                self.permalink,
                                                comments_only=True)
        return self._comments

    @comments.setter  # pylint: disable-msg=E1101
    def comments(self, new_comments):  # pylint: disable-msg=E0102,E0202
        self._update_comments(new_comments)
        self._all_comments = False
        self._comments_flat = None
        self._orphaned = {}

    @property
    def comments_flat(self):
        """
        Return comments as an unnested, flat list.

        Note that there may be instances of MoreComment objects. To
        easily remove these objects, use the all_comments_flat property
        instead.
        """
        if not self._comments_flat:
            stack = self.comments[:]
            self._comments_flat = []
            while len(stack) > 0:
                comment = stack.pop(0)
                assert(comment not in self._comments_flat)
                if isinstance(comment, Comment):
                    stack[0:0] = comment.replies
                self._comments_flat.append(comment)
        return self._comments_flat

    def set_flair(self, *args, **kwargs):
        """Set flair for this submission."""
        return self.subreddit.set_flair(self, *args, **kwargs)

    @property
    def short_link(self):
        """
        Return a short link to the submission.

        The short link points to a page on the short_domain that redirects to
        the main. http://redd.it/y3r8u is a short link for reddit.com.
        """
        return urljoin(self.reddit_session.config.short_domain, self.id)


class Subreddit(Messageable, NSFWable, Refreshable):
    """A class for Subreddits."""
    ban = _modify_relationship('banned', is_sub=True)
    unban = _modify_relationship('banned', unlink=True, is_sub=True)
    make_contributor = _modify_relationship('contributor', is_sub=True)
    remove_contributor = _modify_relationship('contributor', unlink=True,
                                              is_sub=True)
    make_moderator = _modify_relationship('moderator', is_sub=True)
    remove_moderator = _modify_relationship('moderator', unlink=True,
                                            is_sub=True)

    # Generic listing selectors
    get_comments = _get_section('comments')
    get_controversial = _get_sorter('controversial')
    get_hot = _get_sorter('')
    get_new = _get_sorter('new')
    get_top = _get_sorter('top')

    # Explicit listing selectors
    get_controversial_from_all = _get_sorter('controversial', t='all')
    get_controversial_from_day = _get_sorter('controversial', t='day')
    get_controversial_from_hour = _get_sorter('controversial', t='hour')
    get_controversial_from_month = _get_sorter('controversial', t='month')
    get_controversial_from_week = _get_sorter('controversial', t='week')
    get_controversial_from_year = _get_sorter('controversial', t='year')
    get_new_by_date = _get_sorter('new', sort='new')
    get_new_by_rising = _get_sorter('new', sort='rising')
    get_top_from_all = _get_sorter('top', t='all')
    get_top_from_day = _get_sorter('top', t='day')
    get_top_from_hour = _get_sorter('top', t='hour')
    get_top_from_month = _get_sorter('top', t='month')
    get_top_from_week = _get_sorter('top', t='week')
    get_top_from_year = _get_sorter('top', t='year')

    def __init__(self, reddit_session, subreddit_name=None, json_dict=None,
                 fetch=False):
        # Special case for when my_reddits is called as no name is returned so
        # we have to extract the name from the URL.  The URLs are returned as:
        # /r/reddit_name/
        if not subreddit_name:
            subreddit_name = json_dict['url'].split('/')[2]

        info_url = reddit_session.config['subreddit_about'] % subreddit_name
        super(Subreddit, self).__init__(reddit_session, json_dict, fetch,
                                        info_url)
        self.display_name = subreddit_name
        self._url = reddit_session.config['subreddit'] % subreddit_name

    def __unicode__(self):
        """Return the subreddit's display name. E.g. python or askreddit."""
        return self.display_name

    def add_flair_template(self, *args, **kwargs):
        """Add a flair template to this subreddit."""
        return self.reddit_session.add_flair_template(self, *args, **kwargs)

    def clear_all_flair(self):
        """Remove all flair on this subreddit."""
        csv = [{'user': x['user']} for x in self.flair_list()]
        if csv:
            return self.set_flair_csv(csv)
        else:
            return

    def clear_flair_templates(self, *args, **kwargs):
        """Clear flair templates for this subreddit."""
        return self.reddit_session.clear_flair_templates(self, *args, **kwargs)

    def flair_list(self, *args, **kwargs):
        """Return a list of flair for this subreddit."""
        return self.reddit_session.flair_list(self, *args, **kwargs)

    def get_banned(self, *args, **kwargs):
        """Get banned users for this subreddit."""
        return self.reddit_session.get_banned(self, *args, **kwargs)

    def get_contributors(self, *args, **kwargs):
        """Get contributors for this subreddit."""
        return self.reddit_session.get_contributors(self, *args, **kwargs)

    def get_flair(self, *args, **kwargs):
        """Get the flair for a user on this subreddit."""
        return self.reddit_session.get_flair(self, *args, **kwargs)

    def get_moderators(self, *args, **kwargs):
        """Get moderators for this subreddit."""
        return self.reddit_session.get_moderators(self, *args, **kwargs)

    def get_modqueue(self, *args, **kwargs):
        """Get the modqueue on this subreddit."""
        return self.reddit_session.get_modqueue(self, *args, **kwargs)

    def get_reports(self, *args, **kwargs):
        """Get the reported submissions on this subreddit."""
        return self.reddit_session.get_reports(self, *args, **kwargs)

    def get_settings(self, *args, **kwargs):
        """Get the settings for this subreddit."""
        return self.reddit_session.get_settings(self, *args, **kwargs)

    def get_spam(self, *args, **kwargs):
        """Get the spam-filtered items on this subreddit."""
        return self.reddit_session.get_spam(self, *args, **kwargs)

    def get_stylesheet(self, *args, **kwargs):
        """Get the stylesheet and associated images for this subreddit."""
        return self.reddit_session.get_stylesheet(self, *args, **kwargs)

    def search(self, query, *args, **kwargs):
        """Search this subreddit."""
        return self.reddit_session.search(query, self, *args, **kwargs)

    def set_flair(self, *args, **kwargs):
        """Set flair for a particular user or submission on this subreddit."""
        return self.reddit_session.set_flair(self, *args, **kwargs)

    def set_flair_csv(self, *args, **kwargs):
        """Set flair for a group of users all at once on this subreddit."""
        return self.reddit_session.set_flair_csv(self, *args, **kwargs)

    def set_settings(self, *args, **kwargs):
        """Set the settings for this subreddit."""
        return self.reddit_session.set_settings(self, *args, **kwargs)

    def set_stylesheet(self, *args, **kwargs):
        """Set stylesheet for this sub-reddit."""
        return self.reddit_session.set_stylesheet(self, *args, **kwargs)

    def submit(self, *args, **kwargs):
        """Submit a new link to this subreddit."""
        return self.reddit_session.submit(self, *args, **kwargs)

    def subscribe(self):
        """Subscribe to this subreddit."""
        # pylint: disable-msg=W0212
        return self.reddit_session._subscribe(self.content_id)

    def unsubscribe(self):
        """Unsubscribe from this subreddit."""
        # pylint: disable-msg=W0212
        return self.reddit_session._subscribe(self.content_id,
                                              unsubscribe=True)

    def update_settings(self, *args, **kwargs):
        """Update only the settings provided for this subreddit."""
        return self.reddit_session.update_settings(self, *args, **kwargs)


class UserList(RedditContentObject):
    """A list of Redditors. Works just like a regular list."""
    def __init__(self, reddit_session, json_dict=None, fetch=False):
        super(UserList, self).__init__(reddit_session, json_dict, fetch)

        # HACK: Convert children to Redditor instances
        for i in range(len(self.children)):
            tmp = self.children[i]
            redditor = Redditor(reddit_session, tmp['name'], fetch=False)
            redditor.id = tmp['id'].split('_')[1]  # pylint: disable-msg=C0103
            self.children[i] = redditor

    def __contains__(self, item):
        return item in self.children

    def __getitem__(self, index):
        return self.children[index]

    def __iter__(self):
        return self.children.__iter__()

    def __len__(self):
        return len(self.children)
