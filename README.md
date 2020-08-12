# mol-mod-import
Code for importing/deleting Amplicon Sequence Variant (ASV) data and metadata into/from a PostgreSQL database, which in turn is part of the [SBDI molecular module](https://github.com/biodiversitydata-se/mol-mod/blob/taxonid/README.md).

### Branches
See [SBDI molecular module](https://github.com/biodiversitydata-se/mol-mod/blob/taxonid/README.md).

### Conda Environment setup
See [SBDI molecular module](https://github.com/biodiversitydata-se/mol-mod/blob/taxonid/README.md).

### Environmental variables
Required environmental variable DATABASE_URL='postgres+psycopg2://[role]:[pwd]@[host]:[port]/[db-name]' can be set in your Conda environment:
```
conda activate [your-env-name]
# List existing
conda env config vars list
# Set var
conda env config vars set DATABASE_URL=[your-db-url]
conda activate [your-env-name]
# Check var
echo $SECRET_KEY

```

