"""The ingest pod: call records go on disk, into Avro, and into BigQuery every two
minutes. It is apart from the service so that a slow load never touches a person's
request, and so that only this pod holds the key that writes to BigQuery.

One writer per table: BigQuery allows a table 1,500 load jobs a day, and a job every
two minutes is 720, so more pods would split by dataset, not share one.
"""
