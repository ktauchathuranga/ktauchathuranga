import datetime
from dateutil import relativedelta
import requests
import os
from xml.dom import minidom
import time
import hashlib

# Fine-grained personal access token with All Repositories access:
# Account permissions: read:Followers, read:Starring, read:Watching
# Repository permissions: read:Commit statuses, read:Contents, read:Issues, read:Metadata, read:Pull Requests
HEADERS = {'authorization': 'token ' + os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME']  # e.g., 'yourusername'
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}

# Global variable for owner ID, set later
OWNER_ID = None

def daily_readme(birthday):
    """
    Returns the length of time since birth, e.g., 'XX years, XX months, XX days'.
    """
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years, 'year' + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days, 'day' + format_plural(diff.days),
        ' 🎂' if (diff.months == 0 and diff.days == 0) else '')

def format_plural(unit):
    """
    Returns 's' for plural units, e.g., 'days' if unit != 1, else ''.
    """
    return 's' if unit != 1 else ''

def simple_request(func_name, query, variables):
    """
    Makes a GraphQL API request and returns the response, or raises an exception on failure.
    """
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables}, headers=HEADERS)
    if request.status_code == 200:
        return request
    raise Exception(f"{func_name} failed with status {request.status_code}: {request.text}")

def graph_commits(start_date, end_date):
    """
    Returns the total commit count across all repositories using contributionsCollection.
    """
    query_count('graph_commits')
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar {
                    totalContributions
                }
            }
        }
    }'''
    variables = {'start_date': start_date, 'end_date': end_date, 'login': USER_NAME}
    request = simple_request('graph_commits', query, variables)
    return int(request.json()['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions'])

def graph_repos_stars(count_type, owner_affiliation, cursor=None):
    """
    Returns total repository or star count for the user.
    """
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request('graph_repos_stars', query, variables)
    data = request.json()['data']['user']['repositories']
    if count_type == 'repos':
        return data['totalCount']
    elif count_type == 'stars':
        total_stars = sum(node['node']['stargazers']['totalCount'] for node in data['edges'])
        if data['pageInfo']['hasNextPage']:
            return total_stars + graph_repos_stars(count_type, owner_affiliation, data['pageInfo']['endCursor'])
        return total_stars

def recursive_loc(owner, repo_name, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    """
    Fetches all commits from a repository's default branch, counting additions, deletions, and user commits.
    """
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    try:
        request = simple_request('recursive_loc', query, variables)
        response_data = request.json()
    except Exception as e:
        print(f"Error fetching data for {owner}/{repo_name}: {e}")
        return addition_total, deletion_total, my_commits

    if response_data['data']['repository']['defaultBranchRef'] is not None:
        history = response_data['data']['repository']['defaultBranchRef']['target']['history']
        for node in history['edges']:
            if node['node']['author']['user'] == OWNER_ID:
                my_commits += 1
                addition_total += node['node']['additions']
                deletion_total += node['node']['deletions']
        if history['pageInfo']['hasNextPage']:
            return recursive_loc(owner, repo_name, addition_total, deletion_total, my_commits, history['pageInfo']['endCursor'])
        return addition_total, deletion_total, my_commits
    return addition_total, deletion_total, my_commits

def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=[]):
    """
    Queries all repositories the user has access to and calculates total lines of code.
    """
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            defaultBranchRef {
                                target {
                                    ... on Commit {
                                        history {
                                            totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request('loc_query', query, variables)
    data = request.json()['data']['user']['repositories']
    edges += data['edges']
    if data['pageInfo']['hasNextPage']:
        return loc_query(owner_affiliation, comment_size, force_cache, data['pageInfo']['endCursor'], edges)
    return cache_builder(edges, comment_size, force_cache)

def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    """
    Updates the cache file with repository data and calculates total LOC.
    """
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError:
        data = ['# Cache file\n'] * comment_size
        with open(filename, 'w') as f:
            f.writelines(data)

    cache_comment = data[:comment_size]
    data = data[comment_size:]
    if len(data) != len(edges) or force_cache:
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()[comment_size:]

    for index, edge in enumerate(edges):
        repo_hash = hashlib.sha256(edge['node']['nameWithOwner'].encode('utf-8')).hexdigest()
        if index < len(data):
            repo_data = data[index].split()
            if repo_data[0] == repo_hash:
                commit_count = edge['node']['defaultBranchRef']['target']['history']['totalCount'] if edge['node']['defaultBranchRef'] else 0
                if len(repo_data) < 5 or int(repo_data[1]) != commit_count or force_cache:
                    owner, repo_name = edge['node']['nameWithOwner'].split('/')
                    loc = recursive_loc(owner, repo_name)
                    data[index] = f"{repo_hash} {commit_count} {loc[2]} {loc[0]} {loc[1]}\n"
            else:
                data[index] = f"{repo_hash} 0 0 0 0\n"
        else:
            owner, repo_name = edge['node']['nameWithOwner'].split('/')
            loc = recursive_loc(owner, repo_name)
            data.append(f"{repo_hash} {edge['node']['defaultBranchRef']['target']['history']['totalCount'] if edge['node']['defaultBranchRef'] else 0} {loc[2]} {loc[0]} {loc[1]}\n")

    with open(filename, 'w') as f:
        f.writelines(cache_comment + data)

    for line in data:
        loc = line.split()
        loc_add += int(loc[3])
        loc_del += int(loc[4])
    return [loc_add, loc_del, loc_add - loc_del, len(data) == len(edges) and not force_cache]

def flush_cache(edges, filename, comment_size):
    """
    Wipes and rebuilds the cache file.
    """
    with open(filename, 'r') as f:
        cache_comment = f.readlines()[:comment_size]
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        for node in edges:
            f.write(f"{hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest()} 0 0 0 0\n")

def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    """
    Updates an SVG file with user statistics.
    """
    svg = minidom.parse(filename)
    with open(filename, 'w', encoding='utf-8') as f:
        tspan = svg.getElementsByTagName('tspan')
        tspan[30].firstChild.data = age_data
        tspan[65].firstChild.data = repo_data
        tspan[67].firstChild.data = contrib_data
        tspan[69].firstChild.data = commit_data
        tspan[71].firstChild.data = star_data
        tspan[73].firstChild.data = follower_data
        tspan[75].firstChild.data = loc_data[2]
        tspan[76].firstChild.data = loc_data[0] + '++'
        tspan[77].firstChild.data = loc_data[1] + '--'
        f.write(svg.toxml('utf-8').decode('utf-8'))

def user_getter(username):
    """
    Returns the user's ID and account creation date.
    """
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    variables = {'login': username}
    request = simple_request('user_getter', query, variables)
    return {'id': request.json()['data']['user']['id']}, request.json()['data']['user']['createdAt']

def follower_getter(username):
    """
    Returns the user's follower count.
    """
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    request = simple_request('follower_getter', query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])

def query_count(funct_id):
    """
    Increments the API call counter for a function.
    """
    QUERY_COUNT[funct_id] += 1

def perf_counter(funct, *args):
    """
    Measures the execution time of a function.
    """
    start = time.perf_counter()
    result = funct(*args)
    return result, time.perf_counter() - start

def formatter(query_type, difference, funct_return=False, whitespace=0):
    """
    Formats and prints execution time and optionally formats the result.
    """
    unit = 's' if difference > 1 else 'ms'
    value = difference if difference > 1 else difference * 1000
    print(f"{'   ' + query_type + ':':<23}{value:>12.4f} {unit}")
    if whitespace and funct_return is not False:
        return f"{'{:,}'.format(funct_return):<{whitespace}}"
    return funct_return

if __name__ == "__main__":
    print("Calculation times:")
    # Set OWNER_ID and get account creation date
    user_data, user_time = perf_counter(user_getter, USER_NAME)
    global OWNER_ID
    OWNER_ID, acc_date = user_data['id'], user_data[1]
    formatter('account data', user_time)

    # Calculate age
    age_data, age_time = perf_counter(daily_readme, datetime.datetime(2002, 7, 5))  # Replace with your birthdate
    formatter('age calculation', age_time)

    # Calculate LOC
    total_loc, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 1)
    formatter('LOC (cached)' if total_loc[-1] else 'LOC (no cache)', loc_time)

    # Calculate commits
    commit_data, commit_time = perf_counter(graph_commits, acc_date, datetime.datetime.now().isoformat())
    commit_data = formatter('commit counter', commit_time, commit_data, 7)

    # Calculate stars, repos, and contributions
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    star_data = formatter('star counter', star_time, star_data)
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    repo_data = formatter('my repositories', repo_time, repo_data, 2)
    contrib_data, contrib_time = perf_counter(graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    contrib_data = formatter('contributed repos', contrib_time, contrib_data, 2)

    # Calculate followers
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)
    follower_data = formatter('follower counter', follower_time, follower_data, 4)

    # Format LOC data
    total_loc = [f"{total_loc[i]:,}" for i in range(3)] + total_loc[3:]

    # Update SVG files
    svg_overwrite('dark_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc)
    svg_overwrite('light_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc)

    # Print total time and API calls
    total_time = user_time + age_time + loc_time + commit_time + star_time + repo_time + contrib_time + follower_time
    print(f"\033[F\033[F\033[F\033[F\033[F\033[F\033[F\033[FTotal function time:{total_time:>11.4f} s")
    print(f"Total GitHub GraphQL API calls: {sum(QUERY_COUNT.values()):>3}")
    for funct_name, count in QUERY_COUNT.items():
        print(f"   {funct_name:<25}{count:>6}")
