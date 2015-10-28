#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""
__author__ = 'wesc+api@google.com (Wesley Chun)'

from datetime import datetime, time

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import StringMessage
from models import Session
from models import SessionForm
from models import SessionForms

from utils import get_user_id

from settings import WEB_CLIENT_ID

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER"

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

OPERATORS = {
    'EQ': '=',
    'GT': '>',
    'GTEQ': '>=',
    'LT': '<',
    'LTEQ': '<=',
    'NE': '!='
}

FIELDS = {
    'CITY': 'city',
    'TOPIC': 'topics',
    'MONTH': 'month',
    'MAX_ATTENDEES': 'maxAttendees',
}

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_GET_BY_TYPE_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2)
)

SESSION_GET_BY_SPEAKER_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1)
)

SESSION_GET_BY_DATE_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    date=messages.StringField(1)
)

SESSION_GET_BY_NOT_TYPE_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    excludedTypeOfSession=messages.StringField(2)
)

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1)
)

WISHLIST_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1)
)


# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1',
               allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
               scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

    # - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copy_conference_to_form(self, conf, display_name):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if display_name:
            setattr(cf, 'organizerDisplayName', display_name)
        cf.check_initialized()
        return cf

    def _create_conference_object(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = get_user_id(user)

        if not request.name:
            raise endpoints.BadRequestException(
                "Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in
                request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10],
                                                  "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10],
                                                "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(
            params={'email': user.email(), 'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email')

        return request

    @ndb.transactional()
    def _update_conference_object(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = get_user_id(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in
                request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copy_conference_to_form(conf, getattr(prof, 'displayName'))

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
                      http_method='POST', name='createConference')
    def create_conference(self, request):
        """Create new conference."""
        return self._create_conference_object(request)

    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='PUT',
                      name='updateConference')
    def update_conference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._update_conference_object(request)

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='GET',
                      name='getConference')
    def get_conference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copy_conference_to_form(conf, getattr(prof, 'displayName'))

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='getConferencesCreated', http_method='POST',
                      name='getConferencesCreated')
    def get_conferences_created(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = get_user_id(user)
        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copy_conference_to_form(conf,
                                                 getattr(prof, 'displayName'))
                   for conf in confs]
        )

    def _get_query(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._format_filters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"],
                                                   filtr["operator"],
                                                   filtr["value"])
            q = q.filter(formatted_query)
        return q

    def _format_filters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in
                     f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException(
                    "Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException(
                        "Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return inequality_field, formatted_filters

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                      path='queryConferences', http_method='POST',
                      name='queryConferences')
    def query_conferences(self, request):
        """Query for conferences."""
        conferences = self._get_query(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in
                      conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[
                self._copy_conference_to_form(conf, names[conf.organizerUserId])
                for conf in conferences]
        )

    # - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copy_profile_to_form(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name,
                            getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf

    def _get_profile_from_user(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = get_user_id(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key=p_key,
                displayName=user.nickname(),
                mainEmail=user.email(),
                teeShirtSize=str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile  # return Profile

    def _do_profile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._get_profile_from_user()

        # if saveProfile(), process user-modifiable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        if field == 'teeShirtSize':
                            setattr(prof, field, str(val).upper())
                        else:
                            setattr(prof, field, val)
            prof.put()

        # return ProfileForm
        return self._copy_profile_to_form(prof)

    @endpoints.method(message_types.VoidMessage, ProfileForm, path='profile',
                      http_method='GET', name='getProfile')
    def get_profile(self, request):
        """Return user profile."""
        return self._do_profile()

    @endpoints.method(ProfileMiniForm, ProfileForm, path='profile',
                      http_method='POST', name='saveProfile')
    def save_profile(self, request):
        """Update & return user profile."""
        return self._do_profile(request)

    # - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conference_registration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._get_profile_from_user()  # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='conferences/attending', http_method='GET',
                      name='getConferencesToAttend')
    def get_conferences_to_attend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._get_profile_from_user()  # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in
                     prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in
                      conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[
                self._copy_conference_to_form(conf, names[conf.organizerUserId])
                for conf in conferences]
        )

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='POST',
                      name='registerForConference')
    def register_for_conference(self, request):
        """Register user for selected conference."""
        return self._conference_registration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='DELETE',
                      name='unregisterFromConference')
    def unregister_from_conference(self, request):
        """Unregister user for selected conference."""
        return self._conference_registration(request, reg=False)

    # - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cache_announcement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get', http_method='GET',
                      name='getAnnouncement')
    def get_announcement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(
            data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")

    # - - - Sessions - - - - - - - - - - - - - - - - - - - -
    @staticmethod
    def _mem_cache_speaker(speaker, wsck):
        """Cache speaker in Memcache if speaker has more than one session"""
        # Get conference from given websafe key
        conf = ndb.Key(urlsafe=wsck).get()

        # Count number of sessions the speaker is speaking in for the specified conference
        sessions = Session.query(ancestor=conf.key).filter(
            Session.speaker == speaker).fetch()
        session_count = len(sessions)

        # if speaker has more than one session, add to memcache
        if session_count > 1:
            memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY,
                         "Featured Speaker: %s" % speaker)

    def _create_session_object(self, request):
        """Create or update Session object, returning SessionForm/request"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException("Authorization required")

        # get Conference object from request; bail if not found
        c_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        conf = c_key.get()
        if not conf:
            raise endpoints.NotFoundException(
                "No conference found with key: %s" % request.websafeConferenceKey)

        # Check if user is authorized to create sessions for this conference
        user_id = get_user_id(user)
        p_key = ndb.Key(Profile, user_id)

        profile = p_key.get()
        c_owner = conf.key.parent().get()
        if profile is not c_owner:
            raise endpoints.UnauthorizedException(
                "Sessions can be only created by conference owner")

        if not request.name:
            raise endpoints.BadRequestException("Name field required")

        if not request.speaker:
            raise endpoints.BadRequestException("Speaker field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in
                request.all_fields()}
        del data['websafeConferenceKey']
        del data['websafeSessionKey']

        # convert dates and times from strings to Date objects
        if data["date"]:
            data["date"] = datetime.strptime(data["date"][:10],
                                             "%Y-%m-%d").date()

        if data["startTime"]:
            data["startTime"] = datetime.strptime(data["startTime"][:5],
                                                  "%H:%M").time()

        s_id = Session.allocate_ids(size=1, parent=c_key)[0]
        s_key = ndb.Key(Session, s_id, parent=c_key)
        data["key"] = s_key

        session = Session(**data)
        session.put()

        taskqueue.add(params={'speaker': request.speaker,
                              'websafeConferenceKey': request.websafeConferenceKey},
                      url='/tasks/set_featured_speaker')

        return self._copy_session_to_form(session)

    @endpoints.method(SessionForm, SessionForm, path='session',
                      http_method='POST', name='createSession')
    def create_session(self, request):
        """Create new session"""
        return self._create_session_object(request)

    def _copy_session_to_form(self, session):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                # convert Date to date string; just copy others
                if field.name == 'date' or field.name == 'startTime':
                    setattr(sf, field.name, str(getattr(session, field.name)))
                else:
                    setattr(sf, field.name, getattr(session, field.name))
            elif field.name == "websafeSessionKey":
                setattr(sf, field.name, session.key.urlsafe())

        sf.check_initialized()
        return sf

    @endpoints.method(SESSION_GET_REQUEST, SessionForms,
                      path='sessions/{websafeConferenceKey}',
                      http_method='GET', name='getConferenceSessions')
    def get_conference_sessions(self, request):
        """Get all sessions for selected conference"""
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # query all sessions for conference
        sessions = Session.query(ancestor=conf.key).fetch()

        # return set of SessionForm objects per Session
        return SessionForms(
            items=[self._copy_session_to_form(session) for session in sessions]
        )

    @endpoints.method(SESSION_GET_BY_TYPE_REQUEST, SessionForms,
                      path='sessions/{websafeConferenceKey}/type/{typeOfSession}',
                      http_method='GET', name='getConferenceSessionsByType')
    def get_conference_sessions_by_type(self, request):
        """Get all sessions of specified type for selected conference"""
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # query all sessions for conference, filter results by typeOfSession
        sessions = Session.query(ancestor=conf.key).filter(
            Session.typeOfSession == request.typeOfSession).fetch()
        if not sessions:
            raise endpoints.NotFoundException(
                'No sessions found with type: %s' % request.typeOfSession)

        return SessionForms(
            items=[self._copy_session_to_form(session) for session in sessions]
        )

    @endpoints.method(SESSION_GET_BY_SPEAKER_REQUEST, SessionForms,
                      path='sessions/speaker/{speaker}',
                      http_method='GET', name='getSessionsBySpeaker')
    def get_sessions_by_speaker(self, request):
        """Get all sessions for selected speaker"""
        sessions = Session.query(Session.speaker == request.speaker).fetch()

        if not sessions:
            raise endpoints.NotFoundException(
                'No sessions found with speaker: %s' % request.speaker)

        return SessionForms(
            items=[self._copy_session_to_form(session) for session in sessions]
        )

    # - - - Wish List - - - - - - - - - - - - - - - - - - - -
    def _add_session_to_profile_wishlist(self, request, add_session=True):
        """Add Session key to wishlist on user Profile"""
        prof = self._get_profile_from_user()

        # validate that the supplied key belongs to a Session
        wssk = request.websafeSessionKey
        s_key = ndb.Key(urlsafe=wssk)
        s_key_kind = s_key.kind()
        if s_key_kind != "Session":
            raise endpoints.BadRequestException(
                "websafeSessionKey must reference a Session")

        # retrieve session
        session = s_key.get()
        if not session:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % wssk)

        # add session to wish list
        if add_session:
            # check if session already in wish list
            if wssk in prof.sessionKeysWishList:
                raise ConflictException(
                    'This session is already in your wish list')

            prof.sessionKeysWishList.append(wssk)
            retval = True

        else:
            if wssk in prof.sessionKeysWishList:
                prof.sessionKeysWishList.remove(wssk)
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        return BooleanMessage(data=retval)

    @endpoints.method(WISHLIST_POST_REQUEST, BooleanMessage,
                      path='profile/wishlist/{websafeSessionKey}',
                      http_method='POST', name='addSessionToWishlist')
    def add_session_to_wishlist(self, request):
        """Add session to Profile wishlist"""
        return self._add_session_to_profile_wishlist(request)

    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='profile/wishlist', http_method='GET',
                      name='getSessionsWishlist')
    def get_sessions_wishlist(self, request):
        """Get list of sessions in user's wish list"""
        # retrieve sessions
        prof = self._get_profile_from_user()
        session_keys = [ndb.Key(urlsafe=wssk) for wssk in
                        prof.sessionKeysWishList]
        sessions = ndb.get_multi(session_keys)

        if not sessions:
            raise endpoints.NotFoundException('No sessions found in wish list')

        return SessionForms(
            items=[self._copy_session_to_form(session) for session in sessions]
        )

    # - - - Additional Queries - - - - - - - - - - - - - - - - - - - -
    @endpoints.method(SESSION_GET_BY_DATE_REQUEST, SessionForms,
                      path='sessions/date/{date}', http_method='GET',
                      name='getSessionsByDate')
    def get_sessions_on_date(self, request):
        """Get all sessions for specified date"""
        # convert query date from string to date object
        date_query = datetime.strptime(request.date[:10], "%Y-%m-%d").date()

        # query based on date, ordered by time
        sessions = Session.query(Session.date == date_query).order(
            Session.startTime).fetch()

        if not sessions:
            raise endpoints.NotFoundException(
                'No sessions found with date: %s' % request.date)

        return SessionForms(
            items=[self._copy_session_to_form(session) for session in sessions]
        )

    @endpoints.method(SESSION_GET_BY_NOT_TYPE_REQUEST, SessionForms,
                      path='sessions/{websafeConferenceKey}/exclude/{excludedTypeOfSession}',
                      http_method='GET',
                      name='getConferenceSessionsByTypeExcluded')
    def get_sessions_exclude_type(self, request):
        """Get all sessions excluding specified type for selected conference"""
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # query all sessions for conference
        sessions = Session.query(ancestor=conf.key).filter(
            Session.typeOfSession != request.excludedTypeOfSession).fetch()

        if not sessions:
            raise endpoints.NotFoundException(
                'No sessions found for specified request')

        return SessionForms(
            items=[self._copy_session_to_form(session) for session in sessions]
        )

    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='sessions/non-workshop/before-seven',
                      http_method='GET',
                      name='getSessionsNonWorkshopBeforeSeven')
    def get_sessions_non_workshop_before_seven(self, request):
        """Get all sessions that aren't workshops and start before 7:00 PM"""
        # query all for non-workshop sessions
        sessions = Session.query(Session.typeOfSession != "workshop").fetch()

        if not sessions:
            raise endpoints.NotFoundException(
                'No sessions found for specified request')

        # filter out sessions > 7:00 PM
        filtered_sessions = [session for session in sessions if
                             session.startTime and session.startTime < time(19,
                                                                            00)]

        return SessionForms(
            items=[self._copy_session_to_form(session) for session in
                   filtered_sessions]
        )

    # - - - Featured Speaker - - - - - - - - - - - - - - - - - - - -
    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='speaker/featured', http_method='GET',
                      name='getFeaturedSpeaker')
    def get_featured_speaker(self, request):
        """Return Featured Speaker from memcache."""
        return StringMessage(
            data=memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY) or "")


api = endpoints.api_server([ConferenceApi])  # register API
