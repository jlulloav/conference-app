# Conference App
Define a set of api endpoints for managing conferences.

App ID: conference-app-1109

To to perform tests on the production environment:

1. Go to [conference-app-1109.appspot.com](https://conference-app-1109.appspot.com) to see the website
2. Go to [conference-app-1109.appspot.com/_ah/api/explorer](https://conference-app-1109.appspot.com/_ah/api/explorer) to access to the API Explorer

## Running the project locally:

1. [Clone this project](https://github.com/jlulloav/conference-app.git) from GitHub.
1. Download and install the latest version of [Python 2.X.X](https://www.python.org/downloads/).
2. Download and install the latest version of the [Google App Engine SDK for Python](https://cloud.google.com/appengine/downloads?hl=en).
3. Run the App Engine SDK and go to File > Add Existing Application, and select the "conference-app" local repository.
4. Click on the project and then on the "Run" button.
5. You can see if the project is up and running by clicking on the "Logs" button.
6. Once the project is running, go to the url the logs provided to view the website. Ex. http://localhost:yourDefaultPort
7. Add "/_ah/api/explorer" to the end of the previous url to access to API Explorer. Ex. http://localhost:yourDefaultPort/_ah/api/explorer
8. You can access to the datastore using the admin server url provided in the logs. Ex. http://localhost:yourAdminPort and click on the Datastore Viewer to run queries.

## Tasks

### Task 1

#### Endpoints created:

1. getConferenceSessions
2. getConferenceSessionsByType
3. getSessionsBySpeaker
4. createSession

#### Models created:

1. Session
2. SessionForm
3. SessionForms

#### Helper method created:

_create_session_object: convert from Datastore object to SessionForm object

Even though the "speaker" property is a String property. I think the best way to go is to use and entity. Ex. SessionSpeaker entity. 

The "duration" is an Float property representing the length of time in hours. Ex. 1.9h = 1 hours, 54 minutes, 0 seconds

The "highlights" is a repeated String property because it will have more than one highlight.

### Task 2
####Endpoints created:

1. addSessionToWishlist
2. getSessionsWishlist

####  Helper method created:

_add_session_to_profile_wishlist

### Task 3
#### Additional queries created:

1. getSessionsByDate
    * Get all sessions on specified date.
2. getConferenceSessionsByTypeExcluded
    * Get all sessions for specified conference where the user wants to exclude an specific type of session.

### Additional indexes created in the index.yaml:
``` yaml
- kind: Session
  properties:
  - name: date
  - name: startTime

- kind: Session
  ancestor: yes
  properties:
  - name: typeOfSession
```

##### Question
Let's say that you don't like workshops and you don't like sessions after 7 pm. How would you handle a query for all non-workshop sessions before 7 pm? What is the problem for implementing this query? What ways to solve it did you think of?

##### Answer
One of the limitations of the Datastore queries is to use inequalities for multiple properties. The workaround is to combine them in the code. We can do an inequality query and then filter inequality on a different property using python. Ex. I did a query for sessions where typeOfSession != "workshop". Then I used Python to filter sessions where startTime > 9 PM. This can be seen in the endpoint called "getSessionsNonWorkshopBeforeSeven".

### Task 4

#### Task "set_featured_speaker":

* This task runs whenever a session is created.
* The "_mem_cache_speaker" helper method handles the speaker in memcache only if they're speaking in at least two sessions of the same conference.

#### Additional endpoint using memcache
The "getFeaturedSpeaker" endpoint will get the speaker in memcache.