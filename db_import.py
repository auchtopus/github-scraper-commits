import pandas as pd
from typing import *
import os
import sqlite3
import csv

data_root = "/Users/antonsquared/Google_Drive/PLSC_355/github-scraper/data"

def get_val(row, idx):
    try:
        if isinstance(row[idx], str):
            return row[idx].upper().strip()
        return row[idx]
    except Exception as e:
        print(f"Error: {row}\n {idx}\n", e)
        return None


def setup_db(conn):
    # Connect to the database

    # Check if the database already exists
    cur = conn.cursor()

    # Turn on foreign key constraints
    cur.execute('PRAGMA foreign_keys = ON;')
    # Create the organization table

    cur.execute("""
            CREATE TABLE IF NOT EXISTS entity (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE 
            )
        """)

    cur.execute("""
            CREATE TABLE IF NOT EXISTS org (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                org_url TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                FOREIGN KEY (entity_id) REFERENCES entity(id)
            )
        """)

    # Create the user table

    cur.execute("""
            CREATE TABLE IF NOT EXISTS user (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL
            )
        """)

    # Create the repo table

    cur.execute("""
            CREATE TABLE IF NOT EXISTS repo (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                org_id INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL,
                fork BOOLEAN NOT NULL,
                FOREIGN KEY (org_id) REFERENCES org(id)
            )
        """)

    # Create the commit table

    cur.execute("""
            CREATE TABLE IF NOT EXISTS git_commit (
                id INTEGER PRIMARY KEY,
                hash TEXT NOT NULL UNIQUE,
                author_id INTEGER NOT NULL,
                repo_id INTEGER NOT NULL,
                committed_at TIMESTAMP NOT NULL,
                FOREIGN KEY (author_id) REFERENCES user(id),
                FOREIGN KEY (repo_id) REFERENCES repo(id)
            )
        """)

    # Commit the changes and close the connection
    conn.commit()


def entity_id(entity_name: str, conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id FROM entity WHERE name = ?
        """, (entity_name,))
        return cur.fetchone()[0]


def find_files(root):
    commit_q = []
    org_q = []
    repo_q = []
    for dirpath, dirname, filenames in os.walk(root):
        for filename in filenames:
            if "commit_history.csv" in filename:
                commit_q.append(os.path.join(dirpath, filename))
            elif "organizations.csv" in filename:
                org_q.append(os.path.join(dirpath, filename))
            elif "repositories.csv" in filename:
                repo_q.append(os.path.join(dirpath, filename))
    return {'commit_q': commit_q, 'org_q': org_q, 'repo_q': repo_q}


def insert_entities(entity_path, conn):
    entity_query = """INSERT OR IGNORE INTO entity (name) VALUES (?)"""
    cur = conn.cursor()
    df = pd.read_csv(entity_path)
    for i, row in df.iterrows():
        entity_name = get_val(row, "entity_name")
        cur.execute(entity_query, (entity_name,))
    conn.commit()


def insert_organizations(org_q, conn):
    cur = conn.cursor()
    entity_query = """INSERT OR IGNORE INTO entity (name) VALUES (?)"""
    org_query = """INSERT OR IGNORE INTO org (name, org_url, entity_id) 
                    VALUES (
                      ?,
                      ?, 
                     (SELECT id FROM entity WHERE name = ?))"""
    for org_csv in org_q:
        df = pd.read_csv(org_csv)
        for i, row in df.iterrows():
            org_name = get_val(row, "github_org_name")
            org_url = get_val(row, "html_url")
            entity_name = get_val(row, "entity")
            cur.execute(entity_query, (entity_name,))
            cur.execute(org_query, (org_name, org_url, entity_name))
    conn.commit()


def insert_repos(repo_q, conn):
    repo_query = """INSERT OR IGNORE INTO repo (name, org_id, created_at, fork) 
                        VALUES 
                        (?, 
                        (SELECT id FROM org WHERE name = ?), 
                        ?, 
                        ?)"""
    cur = conn.cursor()
    for repo_csv in repo_q:
        print(f"inserting: {repo_csv}")
        df = pd.read_csv(repo_csv)
        for i, row in df.iterrows():
            repo_name = get_val(row, "name")
            repo_org_name = get_val(row, "organization")
            repo_created_at = get_val(row, "created_at")
            repo_fork = get_val(row, "fork")
            repo_data = (repo_name, repo_org_name,
                         repo_created_at, repo_fork)
            cur.execute(repo_query, repo_data)
    conn.commit()


def insert_commits(commit_q, conn):
    commit_author_query = """INSERT OR IGNORE INTO user (name, email) VALUES (?, ?)"""
    commit_query = """INSERT OR IGNORE INTO git_commit (hash, author_id, repo_id, committed_at)
                         VALUES (?, 
                         (SELECT id FROM user WHERE name = ?), 
                         (SELECT id FROM repo WHERE name = ?), 
                         ?)"""
    cur = conn.cursor()
    for commit_csv in commit_q:
        print(f"inserting: {commit_csv}")
        df = pd.read_csv(commit_csv)
        for i, row in df.iterrows():
            commit_hash = get_val(row, "sha")
            commit_author_name = get_val(row, "committer_name")
            commit_author_email = get_val(row, "committer_email")
            commit_repo_name = get_val(row, "repository")
            commit_committed_at = get_val(row, "commited_at")
            # insert the author if they don't exist
            commit_author_data = (commit_author_name, commit_author_email)
            cur.execute(commit_author_query, commit_author_data)
            commit_data = (commit_hash, commit_author_name,
                           commit_repo_name, commit_committed_at)
            cur.execute(commit_query, commit_data)
    conn.commit()


def main():
    # Connect to the database
    conn = sqlite3.connect(os.path.join(data_root, "github_scraper_db.sqlite3"))
    setup_db(conn)
    files = find_files(data_root)
    # insert_entities("/Users/antonsquared/Google_Drive/PLSC_355/github-scraper/entities.csv", conn)
    insert_organizations(files['org_q'], conn)
    insert_repos(files['repo_q'], conn) 
    insert_commits(files['commit_q'], conn)

    #  asserts:
    for table in ["entity", "org", "repo", "user", "git_commit"]:
        query = f"SELECT COUNT(*) FROM {table}"
        print("\n")
        print(query)
        print(conn.cursor().execute(query).fetchone()[0])
        if table != "git_commit":
            query = f"SELECT COUNT(DISTINCT name) FROM {table}"
            print("\n")
            print(conn.cursor().execute(query).fetchone()[0])
        
        

    conn.close()
if __name__ == "__main__":
    main()