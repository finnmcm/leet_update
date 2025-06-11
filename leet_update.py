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
  item = resp.get("Item", {})
  sols = item.get('problems', {})
  score = item.get('score', 0)
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
      print("hi!")
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
  if not newSols:
    return currScore
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
  print(currScore + points)
  return currScore + points

def get_all_users():    
  expr_names = {
    "#u": "username",
    "#num": "number"
  }
  projection = "#u, #num"
  response = TABLE.scan(
  ProjectionExpression=projection,
  ExpressionAttributeNames=expr_names
  )
  items = response.get('Items', [])
  while 'LastEvaluatedKey' in response:
    response = TABLE.scan(
        ProjectionExpression=projection,
        ExpressionAttributeNames=expr_names,
        ExclusiveStartKey=response['LastEvaluatedKey']
    )
    items.extend(response.get('Items', []))
  users = []
  for user in items:
   users.append((user['username'], user['number']))
  return users

def build_scoreboard(username, newUserSolutions, userScores):
  s = "Hi, @" + username + "! Here's your daily LeetUpdate digest: \n\n"
  sortedScores = sorted(userScores.items(), key=lambda kv: kv[1], reverse=True)
  if not newUserSolutions:
    s += "You didn't solve any problems yesterday. Lock in and make it happen today.\n\n"
  else:
    s += "You solved " + str(len(newUserSolutions)) + " problems yesterday! Nice work.\n\n"
    
  s += "Here's the current LeetUpdate scoreboard: \n"
  currUserPosition = -1
  for (pos, user) in enumerate(sortedScores):
    if user[0] == username:
      currUserPosition = pos+1
    line = str(pos+1) + ". " + "@" + user[0] + ": " + str(user[1]) + " points\n"
    s += line
  s += "\n"

  if currUserPosition == 1:
    line = "You're currently in 1st place. Keep it up and stay on top today!"
  elif currUserPosition == 2:
    line = "You're currently in 2nd place. Solve some problems today and take that #1 spot!"
  else:
    line = "You're currently in " + str(currUserPosition) + "th place. Solve some problems today and close the gap!"
  s += line
  return s
  
    
  
      
def run_lambda():
  # MAIN DAILY RUN FUNCTION TO UPDATE SCOREBOARD, TEXT USERS
  newUserSolutions = {}
  userScores = {}
  users = get_all_users()
  for user in users:
    currScore, solvedProblems = fetch_new_sols(user[0])
    newUserSolutions[user[0]] = solvedProblems
    newScore = update_user_db_score(user, solvedProblems, currScore)
    userScores[user[0]] = newScore
  
  for user in users:
    message = build_scoreboard(user[0], newUserSolutions=newUserSolutions[user[0]], userScores=userScores)
    phoneNumber = "+" + user[1]
    client.messages.create(
    body=message,
    from_="+14433329581",
    to=phoneNumber
    )
  


    
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
newUserSolutions = {}
userScores = {}
users = get_all_users()
for user in users:
  currScore, solvedProblems = fetch_new_sols(user[0])
  newUserSolutions[user[0]] = solvedProblems
  newScore = update_user_db_score(user[0], solvedProblems, currScore)
  userScores[user[0]] = newScore
    
#lambda_handler(None, None)
#print(get_all_users())
#print(build_scoreboard("finnmcm", ["Container with Most Water", "Two-sum"], {"finnmcm": 12, "zakuism": 9}))
  

  
