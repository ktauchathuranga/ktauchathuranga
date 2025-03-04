import datetime
import json
from dateutil import relativedelta
import requests
import os
from xml.dom import minidom
import time
import hashlib
import sys

# ----------------------- Configuration -----------------------

# Set DEBUG to True to enable verbose debug output.
DEBUG = True

# GitHub Personal Access Token must be set in environment variable.
HEADERS = {'authorization': 'token ' + os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME']  # e.g., "ktauchathuranga"

# Directory to store cache and metadata.
CACHE_DIR = "cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# Global counter for API calls.
QUERY_COUNT = {
    'user_getter': 0,
    'follower_getter': 0,
    'graph_repos_stars': 0,
    'recursive_loc': 0,
    'graph_commits': 0,
    'loc_query': 0
}

# ----------------------- Debug Function -----------------------

def debug(msg):
    if DEBUG:
        print("[DEBUG]", msg)

# ----------------------- Helper Functions -----------------------

def format_plural(unit):
    return '' if unit == 1 else 's'

def load_metadata():
    meta_path = os.path.join(CACHE_DIR, "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, 'r') as f:
            meta = json.load(f)
        debug("Loaded metadata: " + str(meta))
        return meta
    else:
        # If metadata doesn't exist, set an old timestamp.
        old_ts = "2000-01-01T00:00:00Z"
        debug("No metadata found. Using default timestamp " + old_ts)
        return {"last_update": old_ts}

def save_metadata(new_timestamp):
    meta_path = os.path.join(CACHE_DIR, "meta.json")
    meta = {"last_update": new_timestamp}
    with open(meta_path, 'w') as f:
        json.dump(meta, f)
    debug("Saved new metadata with timestamp " + new_timestamp)

def simple_request(func_name, query, variables):
    debug(f"{func_name}: Sending request with variables {variables}")
    response = requests.post('https://api.github.com/graphql',
                             json={'query': query, 'variables': variables},
                             headers=HEADERS)
    if response.status_code == 200:
        debug(f"{func_name}: Received successful response.")
        return response
    raise Exception(func_name, 'has failed with', response.status_code, response.text, QUERY_COUNT)

def query_count(funct_id):
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1

def perf_counter(func, *args):
    start = time.perf_counter()
    result = func(*args)
    elapsed = time.perf_counter() - start
    debug(f"perf_counter: {func.__name__} took {elapsed:.4f} seconds.")
    return result, elapsed

def formatter(query_type, difference, funct_return=False, whitespace=0):
    print('{:<23}'.format('   ' + query_type + ':'), end='')
    if difference > 1:
        print('{:>12}'.format('%.4f' % difference + ' s '))
    else:
        print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))
    if whitespace:
        return f"{'{:,}'.format(funct_return): <{whitespace}}"
    return funct_return

def force_close_file(data, cache_comment, filename):
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    debug(f"force_close_file: Partial data saved to {filename}")

# ----------------------- Core Functions -----------------------

def daily_readme(birthday):
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    result = '{} {}, {} {}, {} {}{}'.format(
        diff.years, 'year' + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days, 'day' + format_plural(diff.days),
        ' 🎂' if (diff.months == 0 and diff.days == 0) else '')
    debug("daily_readme: " + result)
    return result

def user_getter(username):
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    variables = {'login': username}
    debug("user_getter: Fetching user data for " + username)
    response = simple_request("user_getter", query, variables)
    user_data = response.json()['data']['user']
    debug("user_getter: Received user ID " + user_data['id'])
    return {'id': user_data['id']}, user_data['createdAt']

def follower_getter(username):
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    debug("follower_getter: Fetching followers for " + username)
    response = simple_request("follower_getter", query, {'login': username})
    count = int(response.json()['data']['user']['followers']['totalCount'])
    debug("follower_getter: " + str(count) + " followers found.")
    return count

def graph_repos_stars(count_type, owner_affiliation, cursor=None):
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
                            updatedAt
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
    debug(f"graph_repos_stars: Fetching with cursor {cursor} for affiliation {owner_affiliation}")
    response = simple_request("graph_repos_stars", query, variables)
    data = response.json()['data']['user']['repositories']
    if count_type == 'repos':
        count = data['totalCount']
        debug("graph_repos_stars: Repo count = " + str(count))
        return count
    elif count_type == 'stars':
        total = 0
        for edge in data['edges']:
            total += edge['node']['stargazers']['totalCount']
        debug("graph_repos_stars: Total stars = " + str(total))
        return total

def recursive_loc(owner, repo_name, data, cache_comment, cursor=None, addition_total=0, deletion_total=0, my_commits=0):
    debug(f"recursive_loc: Starting for {owner}/{repo_name} with cursor {cursor}")
    while True:
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
                                        committedDate
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
        debug(f"recursive_loc: Querying commits with variables {variables}")
        response = requests.post('https://api.github.com/graphql',
                                 json={'query': query, 'variables': variables},
                                 headers=HEADERS)
        if response.status_code == 200:
            response_data = response.json()['data']['repository']
            if response_data and response_data['defaultBranchRef'] is not None:
                history = response_data['defaultBranchRef']['target']['history']
                debug(f"recursive_loc: Fetched {len(history['edges'])} commits")
                for edge in history['edges']:
                    node = edge['node']
                    if (node.get('author') and node['author'].get('user') and 
                        node['author']['user']['id'] == OWNER_ID):
                        my_commits += 1
                        addition_total += node['additions']
                        deletion_total += node['deletions']
                if not history['pageInfo']['hasNextPage']:
                    debug("recursive_loc: No more pages, finishing.")
                    break
                else:
                    cursor = history['pageInfo']['endCursor']
                    debug(f"recursive_loc: Moving to next page with cursor {cursor}")
            else:
                debug(f"recursive_loc: Repository {owner}/{repo_name} is empty or missing default branch.")
                return (0, 0, 0)
        else:
            # Save partial data and then raise an error.
            filename = os.path.join(CACHE_DIR, hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt')
            force_close_file(data, cache_comment, filename)
            if response.status_code == 403:
                raise Exception("Too many requests! You've hit the anti-abuse limit!")
            raise Exception(f'recursive_loc() failed with status: {response.status_code}, response: {response.text}')
    debug(f"recursive_loc: Completed for {owner}/{repo_name} -> commits: {my_commits}, additions: {addition_total}, deletions: {deletion_total}")
    return addition_total, deletion_total, my_commits

def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=[], cache_suffix=""):
    query_count('loc_query')
    debug(f"loc_query{cache_suffix}: Fetching repositories with cursor {cursor} for affiliation {owner_affiliation}")
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            updatedAt
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
    response = simple_request("loc_query", query, variables)
    repos_data = response.json()['data']['user']['repositories']
    debug(f"loc_query{cache_suffix}: Retrieved {len(repos_data['edges'])} repositories")
    if repos_data['pageInfo']['hasNextPage']:
        edges += repos_data['edges']
        return loc_query(owner_affiliation, comment_size, force_cache, repos_data['pageInfo']['endCursor'], edges, cache_suffix)
    else:
        return cache_builder(edges + repos_data['edges'], comment_size, force_cache, cache_suffix)

def cache_builder(edges, comment_size, force_cache, cache_suffix):
    debug(f"cache_builder{cache_suffix}: Building cache...")
    cached = True
    filename = os.path.join(CACHE_DIR, hashlib.sha256((USER_NAME + cache_suffix).encode('utf-8')).hexdigest() + '.txt')
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
        debug(f"cache_builder{cache_suffix}: Cache file found.")
    except FileNotFoundError:
        debug(f"cache_builder{cache_suffix}: Cache file not found. Creating new cache.")
        data = []
        if comment_size > 0:
            for _ in range(comment_size):
                data.append('This line is a comment block. Write whatever you want here.\n')
        with open(filename, 'w') as f:
            f.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        debug(f"cache_builder{cache_suffix}: Cache rebuild needed.")
        cached = False
        flush_cache(edges, filename, comment_size, cache_suffix)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size]
    data = data[comment_size:]
    new_data = []
    for index in range(len(edges)):
        current_hash = hashlib.sha256(edges[index]['node']['nameWithOwner'].encode('utf-8')).hexdigest()
        # If existing entry exists, update if needed.
        try:
            repo_hash, commit_count, *rest = data[index].split()
        except IndexError:
            repo_hash = ""
        if repo_hash == current_hash:
            try:
                expected_commits = edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']
                if int(commit_count) != expected_commits:
                    debug(f"cache_builder{cache_suffix}: Repository {edges[index]['node']['nameWithOwner']} updated. Recalculating LOC.")
                    owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')
                    loc = recursive_loc(owner, repo_name, data, cache_comment)
                    new_data.append(current_hash + ' ' + str(expected_commits) + ' ' + str(loc[2]) + ' ' + str(loc[0]) + ' ' + str(loc[1]) + '\n')
                else:
                    new_data.append(data[index])
            except (TypeError, IndexError):
                new_data.append(current_hash + ' 0 0 0 0\n')
        else:
            # New repository not in cache; calculate full data.
            debug(f"cache_builder{cache_suffix}: New repository found: {edges[index]['node']['nameWithOwner']}. Calculating data.")
            owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')
            loc = recursive_loc(owner, repo_name, data, cache_comment)
            expected_commits = edges[index]['node']['defaultBranchRef']['target']['history']['totalCount'] if edges[index]['node']['defaultBranchRef'] else 0
            new_data.append(current_hash + ' ' + str(expected_commits) + ' ' + str(loc[2]) + ' ' + str(loc[0]) + ' ' + str(loc[1]) + '\n')
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(new_data)
    loc_add, loc_del = 0, 0
    for line in new_data:
        loc_values = line.split()
        loc_add += int(loc_values[3])
        loc_del += int(loc_values[4])
    debug(f"cache_builder{cache_suffix}: Cache build complete.")
    return [loc_add, loc_del, loc_add - loc_del, cached]

def flush_cache(edges, filename, comment_size, cache_suffix):
    debug(f"flush_cache{cache_suffix}: Flushing and rebuilding cache...")
    with open(filename, 'r') as f:
        data = []
        if comment_size > 0:
            data = f.readlines()[:comment_size]
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            f.write(hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0 0\n')
    debug(f"flush_cache{cache_suffix}: Cache flush complete.")

def commit_counter(comment_size, cache_suffix=""):
    total_commits = 0
    filename = os.path.join(CACHE_DIR, hashlib.sha256((USER_NAME + cache_suffix).encode('utf-8')).hexdigest() + '.txt')
    with open(filename, 'r') as f:
        data = f.readlines()[comment_size:]
    for line in data:
        total_commits += int(line.split()[2])
    debug(f"commit_counter{cache_suffix}: Total commits counted = {total_commits}")
    return total_commits

def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    debug(f"svg_overwrite: Overwriting SVG file {filename}")
    svg = minidom.parse(filename)
    with open(filename, mode='w', encoding='utf-8') as f:
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
    debug(f"svg_overwrite: Finished updating {filename}")

def svg_element_getter(filename):
    svg = minidom.parse(filename)
    tspan = svg.getElementsByTagName('tspan')
    for index in range(len(tspan)):
        debug(f"svg_element_getter: Element {index} - {tspan[index].firstChild.data}")

# ----------------------- Incremental Update Functions -----------------------

def get_repos_updated_since(last_update, owner_affiliation):
    """
    Returns a list of repository nodes (with nameWithOwner and updatedAt)
    that have been updated after last_update.
    """
    query = '''
    query($login: String!) {
      user(login: $login) {
        repositories(first: 100, ownerAffiliations: %s) {
          edges {
            node {
              nameWithOwner
              updatedAt
            }
          }
        }
      }
    }''' % (json.dumps(owner_affiliation))
    variables = {'login': USER_NAME}
    response = simple_request("get_repos_updated_since", query, variables)
    updated_repos = []
    for edge in response.json()['data']['user']['repositories']['edges']:
        repo = edge['node']
        if repo['updatedAt'] > last_update:
            updated_repos.append(repo)
    debug(f"get_repos_updated_since: {len(updated_repos)} repos updated since {last_update}")
    return updated_repos

def update_cache_for_repo(repo, cache_suffix, comment_size=7):
    """
    For a given repo (dict with at least nameWithOwner), update its cache entry.
    """
    owner, repo_name = repo['nameWithOwner'].split('/')
    # For simplicity, we recalc using recursive_loc
    updated_data = recursive_loc(owner, repo_name, [], [])
    # Get expected commit count if available.
    expected_commits = 0
    # To get expected_commits, you might need to call a lightweight query;
    # here we assume recursive_loc already did it.
    expected_commits = updated_data[2]
    current_hash = hashlib.sha256(repo['nameWithOwner'].encode('utf-8')).hexdigest()
    new_entry = current_hash + ' ' + str(expected_commits) + ' ' + str(updated_data[2]) + ' ' + str(updated_data[0]) + ' ' + str(updated_data[1]) + '\n'
    debug(f"update_cache_for_repo: Updated {repo['nameWithOwner']} with new entry: {new_entry.strip()}")
    return new_entry

def incremental_cache_update(cache_suffix, owner_affiliation, comment_size=7, force_cache=False):
    """
    Loads the existing cache, checks for repositories updated since the last update,
    and updates only those entries. Then saves the updated cache and metadata.
    """
    meta = load_metadata()
    last_update = meta.get("last_update")
    # Get the list of repos updated since last_update.
    updated_repos = get_repos_updated_since(last_update, owner_affiliation)
    # Build the cache file path.
    filename = os.path.join(CACHE_DIR, hashlib.sha256((USER_NAME + cache_suffix).encode('utf-8')).hexdigest() + '.txt')
    # Load existing cache data.
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError:
        debug("Cache file not found for incremental update. Running full cache rebuild.")
        # If no cache, run full cache.
        return loc_query(owner_affiliation, comment_size, force_cache, cache_suffix=cache_suffix)
    
    # Convert cache to a dict for quick lookup.
    cache_dict = {}
    # Skip comment block.
    for line in data[comment_size:]:
        parts = line.split()
        if parts:
            cache_dict[parts[0]] = line
    # For each updated repo, update its entry.
    for repo in updated_repos:
        current_hash = hashlib.sha256(repo['nameWithOwner'].encode('utf-8')).hexdigest()
        new_entry = update_cache_for_repo(repo, cache_suffix, comment_size)
        cache_dict[current_hash] = new_entry
    # Rebuild cache lines preserving comment block.
    comment_block = data[:comment_size] if len(data) >= comment_size else []
    new_cache_lines = comment_block + list(cache_dict.values())
    with open(filename, 'w') as f:
        f.writelines(new_cache_lines)
    # Update metadata with current time.
    new_timestamp = datetime.datetime.utcnow().isoformat() + "Z"
    save_metadata(new_timestamp)
    # Return aggregated LOC values (example: sum of added, deleted, diff)
    loc_add, loc_del = 0, 0
    for line in new_cache_lines[comment_size:]:
        parts = line.split()
        if len(parts) >= 5:
            loc_add += int(parts[3])
            loc_del += int(parts[4])
    debug(f"incremental_cache_update{cache_suffix}: Updated cache. Total LOC added: {loc_add}, deleted: {loc_del}")
    return [loc_add, loc_del, loc_add - loc_del, True]

# ----------------------- Main Execution -----------------------

if __name__ == '__main__':
    # Usage:
    #   --full-cache         Rebuild the full cache from scratch.
    #   --incremental-update Run incremental update (using metadata and only updated repos)
    mode = None
    if len(sys.argv) > 1:
        if sys.argv[1] == "--full-cache":
            mode = "full"
            debug("Running in full cache mode.")
        elif sys.argv[1] == "--incremental-update":
            mode = "incremental"
            debug("Running in incremental update mode.")
    if mode is None:
        print("Usage: python today.py --full-cache | --incremental-update")
        sys.exit(1)

    print("Calculation times:")
    (user_info, created_at), user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID = user_info['id']
    age_data, age_time = perf_counter(daily_readme, datetime.datetime(2002, 7, 5))

    # For owned repositories (your own repos)
    if mode == "full":
        owned_loc, owned_loc_time = perf_counter(loc_query, ['OWNER'], 7, True, None, [], "_owner")
    else:
        owned_loc, owned_loc_time = perf_counter(incremental_cache_update, "_owner", ['OWNER'], 7, False)
    owned_commit_data = commit_counter(7, "_owner")

    # For contributed repositories (repos you contributed to, not owned)
    if mode == "full":
        contrib_loc, contrib_loc_time = perf_counter(loc_query, ['COLLABORATOR', 'ORGANIZATION_MEMBER'], 7, True, None, [], "_contrib")
    else:
        contrib_loc, contrib_loc_time = perf_counter(incremental_cache_update, "_contrib", ['COLLABORATOR', 'ORGANIZATION_MEMBER'], 7, False)
    contrib_commit_data = commit_counter(7, "_contrib")

    # Repository counts
    repo_data = perf_counter(graph_repos_stars, 'repos', ['OWNER'])[0]
    contrib_repo_data = perf_counter(graph_repos_stars, 'repos', ['COLLABORATOR', 'ORGANIZATION_MEMBER'])[0]

    # Lifetime commits (all contributions; note this includes both owned and contributed)
    lifetime_commit_data, lifetime_commit_time = perf_counter(lambda: 0)  # Placeholder if not needed

    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)

    # Format numbers for display (for SVG update)
    repo_data = formatter('my repositories', 0, repo_data, 2)
    contrib_repo_data = formatter('contributed repos', 0, contrib_repo_data, 2)
    star_data = formatter('star counter', star_time, star_data)
    # We show contributed commit count (non-owned) as requested.
    contrib_commit_data = formatter('contrib commits', contrib_loc_time, contrib_commit_data, 7)

    # Format LOC numbers (using contributed LOC here as an example)
    for index in range(len(contrib_loc) - 1):
        contrib_loc[index] = '{:,}'.format(contrib_loc[index])

    # Update SVG files (assuming you have dark_mode.svg and light_mode.svg in your repo)
    svg_overwrite('dark_mode.svg', age_data, contrib_commit_data, star_data, str(repo_data), str(contrib_repo_data), str(follower_data), contrib_loc[:-1])
    svg_overwrite('light_mode.svg', age_data, contrib_commit_data, star_data, str(repo_data), str(contrib_repo_data), str(follower_data), contrib_loc[:-1])

    total_func_time = user_time + age_time + owned_loc_time + contrib_loc_time + star_time + follower_time
    print('\033[F' * 8, '{:<21}'.format('Total function time:'), '{:>11}'.format('%.4f' % total_func_time), ' s')
    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items():
        print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))
