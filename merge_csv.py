import pandas as pd
import os


merged_df = pd.DataFrame()
merge_dir = "/Users/antonsquared/Google_Drive/PLSC_355/github-scraper/data/2023-04-24_12-20-23"
for path in os.listdir(merge_dir):
    read_path = os.path.join(merge_dir, path)
    print(f"{read_path=}")
    org_df = pd.read_csv(read_path)
    merged_df = pd.concat([merged_df, org_df])
    print(len(merged_df))


merged_df.to_csv(os.path.join(merge_dir, "org_repositories.csv"), index=False)
