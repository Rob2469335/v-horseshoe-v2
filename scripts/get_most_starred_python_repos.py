import requests
import json
from datetime import datetime, timedelta

# GitHub API endpoint for trending repositories
GITHUB_API_URL = "https://api.github.com/search/repositories"

# Parameters for the query
params = {
    "q": "language:python created:>{date} sort:stars-desc",
    "per_page": 10,  # Limit to top 10 most starred
    "page": 1,
}

# Calculate date 7 days ago
seven_days_ago = datetime.now() - timedelta(days=7)

# Format date as ISO string for query
date_str = seven_days_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
params["q"] = params["q"].format(date_str=date_str)

# Make the API request
response = requests.get(GITHUB_API_URL, params=params)

# Check if the request was successful
if response.status_code == 200:
    data = response.json()
    # Extract the repositories
    repos = [
        {
            "name": repo["name"],
            "full_name": repo["full_name"],
            "stargazers_count": repo["stargazers_count"],
            "html_url": repo["html_url"],
            "description": repo.get("description", ""),
        }
        for repo in data.get("items", [])
    ]

    # Save to trends.json
    with open("trends.json", "w") as f:
        json.dump(repos, f, indent=4)

    print(f"Successfully saved {len(repos)} most starred Python repos to trends.json")
else:
    print(f"Error fetching data: {response.status_code} - {response.text}")

# Optional: Print the results
if "items" in data:
    print("\nTop 10 most starred Python repos in the last 7 days:\n")
    for i, repo in enumerate(data["items"][:10], 1):
        print(f"{i}. {repo['name']} ({repo['stargazers_count']} stars)")
else:
    print("No data found.")
