import json
import requests
#from botocore.vendored import requests
import boto3
from botocore.exceptions import ClientError
import time
from boto3.dynamodb.conditions import Key

import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()


dynamodb = boto3.resource('dynamodb')
TABLE = dynamodb.Table('leetupdate')
GRAPH_QL = "https://leetcode.com/graphql"

account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
twilio_num  = os.getenv("TWILIO_PHONE_NUMBER")

client = Client(account_sid, auth_token)


def scrape_leet_sols(username):
    submission_query = """
    query recentAcSubmissionList($username: String!, $limit: Int!) {
      recentAcSubmissionList(username: $username, limit: $limit) {
        title
        titleSlug
        timestamp
      }
    }
    """
    variables = {"username": username, "limit": 20}

    resp = requests.post(GRAPH_QL, json={"query": submission_query, "variables": variables})
    resp.raise_for_status()
    data = resp.json()
    problems = {}
    for p in data['data']['recentAcSubmissionList']:
      problems[p['title']] = p['titleSlug']

    return problems

def get_user_data(username):
  resp = TABLE.get_item(Key={'username': username})
  sols = resp.get('problems', {})
  score = resp.get('score', 0)
  return [sols, score]

def update_user_db_sols(username, sols):
  try:
    resp = TABLE.update_item(
            Key={ 'username': username},
            UpdateExpression="SET #P = :pval",
            ExpressionAttributeNames={ "#P": "problems" },
            ExpressionAttributeValues={ ":pval": sols},
            ReturnValues="UPDATED_NEW"
        )
  except ClientError as e:
      print("Update failed:", e.response['Error']['Message'])
      return False

    # 'UPDATED_NEW' returns only the attributes that were changed, e.g. {'status': 'shipped'}
  updated_attrs = resp.get('Attributes', {})
  print("Successfully updates user DB solution data")
  return True

def fetch_new_sols(username):
  query = """
    query getQuestionDetail($titleSlug: String!) {
      question(titleSlug: $titleSlug) {
        difficulty
      }
    }
    """
  headers = {"Content-Type": "application/json"}
  #old data
  userData = get_user_data(username)
  currScore = userData[1]
  currSols = userData[0]
  #new data
  newSols = scrape_leet_sols(username)
  solsWithDifficulties = {}
  for problem in newSols:
    #if problem isn't currently in db, it was solved in the previous day
    if problem not in currSols:
      var = {"titleSlug" : newSols[problem]}
      response = requests.post(
        GRAPH_QL,
        json={"query": query, "variables": var},
        headers=headers
    )
      response.raise_for_status()
      data = response.json()
      solsWithDifficulties[problem] = data['data']['question']['difficulty']
  update_user_db_sols(username, newSols)
  return (currScore, solsWithDifficulties)
      

    
def create_user(username, phoneNumber):

  # CREATE USER: add a new user to the table with their 20 most recently solved problems
  userSolutions = scrape_leet_sols(username=username)
  if(len(userSolutions) == 0):
    print("user has not solved any problems")
    return False
  
  userData = {
    'username':  username,
    'score': 0,
    'number': phoneNumber,
    'problems':   userSolutions,
}
  try:
    res = TABLE.put_item(Item=userData)
    status_code = res['ResponseMetadata']['HTTPStatusCode']
    if status_code == 200:
        print("User successfully added!")
        return True
    else:
        print(f"PutItem returned HTTP {status_code} (unexpected).")
        return False
  except ClientError as e:  
      print("PutItem failed:", e.response['Error']['Message'])
      return False

def update_user_db_score(username, newSols, currScore):
  points = 0
  for k in newSols:
    if newSols[k] == 'Easy':
      points += 1
    elif newSols[k] == 'Medium':
      points += 2
    else:
      points += 5
      
  try:
    resp = TABLE.update_item(
            Key={ 'username': username},
            UpdateExpression="SET #S = :val",
            ExpressionAttributeNames={ "#S": "score" },
            ExpressionAttributeValues={ ":val": currScore + points },
            ReturnValues="UPDATED_NEW"
        )
  except ClientError as e:
      print("Update failed:", e.response['Error']['Message'])
      return None

    # 'UPDATED_NEW' returns only the attributes that were changed, e.g. {'status': 'shipped'}
  updated_attrs = resp.get('Attributes', {})
  print("Successfully updated user @" + username + "'s score: " , updated_attrs)
  return currScore + points

def get_all_users():    
  keys = []
  last_evaluated_key = None

  while True:
        try:
            if last_evaluated_key:
                resp = TABLE.scan(
                    ProjectionExpression="#U",
                    ExpressionAttributeNames={
                        "#U": "username",
                    },
                    ExclusiveStartKey=last_evaluated_key
                )
            else:
                resp = TABLE.scan(
                    ProjectionExpression="#U",
                    ExpressionAttributeNames={
                        "#U": "username",
                    }
                )
        except ClientError as e:
            print("Failed to retrieve usernames:", e.response['Error']['Message'])
            return []
        item = resp.get('Items', [])
        keys.extend(item)
        last_evaluated_key = resp.get('LastEvaluatedKey')
        if not last_evaluated_key:
            break
  parsedUsernames = [key['username'] for key in keys]
  return parsedUsernames

def send_scoreboard():
  pass    
      
def run_lambda():
  # MAIN DAILY RUN FUNCTION TO UPDATE SCOREBOARD, TEXT USERS
  totalUserSolutions = {}
  userScores = {}
  users = get_all_users()
  for user in users:
    currScore, solvedProblems = fetch_new_sols(user)
    totalUserSolutions[user] = solvedProblems
    newScore = update_user_db_score(user, solvedProblems, currScore)
    userScores[user] = newScore
    
def lambda_handler(event, context):
  run_lambda()
  '''
    # TODO implement
    return {
        'statusCode': 200,
        'body': json.dumps('Hello from Lambda!')
    }
    
    totalUserSolutions = {}
    userScores = {}
    users = get_all_users()
    for user in users:
      currScore, solvedProblems = fetch_new_sols(user)
      totalUserSolutions[user] = solvedProblems
      newScore = update_user_db_score(user, solvedProblems, currScore)
     userScores[user] = newScore
    '''
#lambda_handler(None, None)
print(account_sid)
print(auth_token)
'''
message = client.messages.create(
    body="Hello from Twilio!",
    from_="+14151234567",
    to="+16176506561"
)
print(message.sid)
'''
  
