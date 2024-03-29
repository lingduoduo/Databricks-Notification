# Databricks notebook source
# MAGIC %md 
# MAGIC ### Setup Bronze Table Connection

# COMMAND ----------

dbutils.credentials.showCurrentRole()

# COMMAND ----------

DATA_OUTPUT_PATH = '/mnt/tmg-prod-datalake-outputs'
dbutils.fs.ls(DATA_OUTPUT_PATH)

# COMMAND ----------

# Move file from driver to DBFS
user = dbutils.notebook.entry_point.getDbutils().notebook().getContext().tags().apply('user')

# COMMAND ----------

# import libraries
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import IntegerType, StringType, BooleanType, DateType, DoubleType
from pyspark.sql.window import Window

# COMMAND ----------

# bronze_df = spark.sql("select * from ml_push.l7_push_meetme_source_partitioned where calculated_time > CURRENT_TIMESTAMP - INTERVAL '1' HOUR")
# bronze_df = spark.sql("select * from ml_push.l7_push_meetme_source_partitioned where calculated_time = (select max(calculated_time) from ml_push.l7_push_meetme_source_partitioned)")
# bronze_df = spark.sql("select * from ml_push.l7_push_meetme_source_partitioned where calculated_time = '2022-10-31 19:20:06.243'")
bronze_df = spark.sql("select * from ml_push.l7_push_meetme_source_partitioned where calculated_time = '2022-11-04 19:19:55.364'")

# COMMAND ----------

bronze_df = bronze_df.withColumn('utc_day_of_week', F.dayofweek(F.from_unixtime(F.col('send_ts')/1000).cast(DateType()))) \
    .withColumn('utc_hour', F.hour(F.from_unixtime(F.col('send_ts')/1000))) \
    .withColumn('broadcaster_id', F.concat(F.col('from_user_network'), F.lit(':user:'), F.col('from_user_id')))

# COMMAND ----------

bronze_df.count()

# COMMAND ----------

bronze_df.printSchema()

# COMMAND ----------

rows = bronze_df.groupBy('network_user_id').count()
rows.count()

# COMMAND ----------

rows = bronze_df.groupBy('from_user_id').count()
rows.count()

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC ### Sampling Methods

# COMMAND ----------

# 1. Raw dataset
bronze_df_sample = bronze_df

# COMMAND ----------

bronze_df_sample.count()

# COMMAND ----------

# MAGIC %md 
# MAGIC Experiment Results - https://dbc-6e4f74ab-0d7d.cloud.databricks.com/?o=1615526246868093#mlflow/experiments/3345621877632982

# COMMAND ----------

# 2. Simple random sampling without replacement
bronze_df_sample = bronze_df.sample(False, 0.05, 28)

# COMMAND ----------

bronze_df_sample.count()

# COMMAND ----------

# MAGIC %md
# MAGIC Experiment Results -

# COMMAND ----------

# 3. Stratified Sampling
bronze_df_sample = bronze_df.sampleBy('open_flag', {0:0.01, 1:1}, seed=28)

# COMMAND ----------

bronze_df_sample.count()

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC Experiment Results - 

# COMMAND ----------

# 4. Filter outliers - broadcasters without one open
bronze_df_pos = (bronze_df.filter(bronze_df.open_flag == 1)
.groupBy('broadcaster_id')
.count())

# COMMAND ----------

bronze_df_sample = (bronze_df.join(bronze_df_pos, [bronze_df.broadcaster_id == bronze_df_pos.broadcaster_id], how='inner')
.select(bronze_df['*'])
.sampleBy('open_flag', {0:0.02, 1:1}, seed=28))

# COMMAND ----------

bronze_df_sample.count()

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC Experiment Results - 

# COMMAND ----------

# 5. Proportional sampling using broadcaster_id
bronze_df_pos = bronze_df.filter(bronze_df.open_flag == 1)
bronze_df_neg = bronze_df.filter(bronze_df.open_flag == 0)               
tot = bronze_df_neg.count()
bronze_df_ratios = bronze_df_neg.withColumn('ratio', F.lit(50/tot)).groupBy('broadcaster_id').agg(F.sum('ratio').alias('ratio'))
d = {r['broadcaster_id']: min(1, r['ratio']) for r in bronze_df_ratios.collect()}
bronze_df_sample = bronze_df_neg.sampleBy('broadcaster_id', d, seed=28).union(bronze_df_pos)

# COMMAND ----------

bronze_df_sample.count()

# COMMAND ----------

# MAGIC %md
# MAGIC Experiment Results - https://dbc-6e4f74ab-0d7d.cloud.databricks.com/?o=1615526246868093#mlflow/experiments/3345621877634310

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC ###Feature Engineering

# COMMAND ----------

silver_df = bronze_df_sample.withColumn('open_flag', F.col('open_flag').cast(BooleanType()))

# COMMAND ----------

for col_name in ['utc_day_of_week', 'utc_hour']:
    silver_df = silver_df.withColumn(col_name, F.col(col_name).cast(StringType()))

# COMMAND ----------

columns = [
    'open_flag',
    'utc_day_of_week',
    'utc_hour',
    'broadcaster_id',
    'network_user_id'
]

# COMMAND ----------

silver_df.count()

# COMMAND ----------

df_train, df_val = silver_df.select(columns).randomSplit([0.8, 0.2], seed=12345)

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC ### Creating Silver Delta Table from Spark Dataframe

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC Save our `train` and `val` datasets into delta lake tables for future use.

# COMMAND ----------

import shutil
# Drop any old delta lake files if needed (e.g. re-running this notebook with the same bronze_tbl_path and silver_tbl_path)
# shutil.rmtree('/dbfs'+silver_train_tbl_path, ignore_errors=True)
# shutil.rmtree('/dbfs'+silver_val_tbl_path, ignore_errors=True)

shutil.rmtree('/dbfs'+silver_train_tbl_path)
shutil.rmtree('/dbfs'+silver_val_tbl_path)

# COMMAND ----------

dbutils.fs.rm('/mnt/tmg-prod-datalake-outputs/push_data/silver_train', recurse=True)

# COMMAND ----------

dbutils.fs.ls('/mnt/tmg-prod-datalake-outputs/push_data/silver_train')

# COMMAND ----------

import uuid

uid = uuid.uuid4().hex[:6]

# COMMAND ----------

database_name = 'ml_push'

# no permissions to overwrite
# silver_train_tbl_path = '{}/push_data/silver_train'.format(DATA_OUTPUT_PATH)
# silver_val_tbl_path = '{}/push_data/silver_train'.format(DATA_OUTPUT_PATH)

silver_train_tbl_path = '{}/push_data/silver_train/{}'.format(DATA_OUTPUT_PATH, uid)
silver_val_tbl_path = '{}/push_data/silver_val/{}'.format(DATA_OUTPUT_PATH, uid)

silver_train_tbl_name = 'silver_l7_push_meetme_train_userid_{}'.format(uid)
silver_val_tbl_name = 'silver_l7_push_meetme_val_userid_{}'.format(uid)

# COMMAND ----------

df_train.write.format('delta').mode('overwrite').option("overwriteSchema", "true").save(silver_train_tbl_path)

# COMMAND ----------

df_val.write.format('delta').mode('overwrite').option("overwriteSchema", "true").save(silver_val_tbl_path)

# COMMAND ----------

spark.sql('''
  CREATE EXTERNAL TABLE IF NOT EXISTS `{}`.{}
  USING DELTA 
  LOCATION '{}'
  '''.format(database_name,silver_train_tbl_name,silver_train_tbl_path))

spark.sql('''
  CREATE EXTERNAL TABLE IF NOT EXISTS `{}`.{}
  USING DELTA 
  LOCATION '{}'
  '''.format(database_name,silver_val_tbl_name,silver_val_tbl_path))

# COMMAND ----------

# MAGIC %md
# MAGIC #### Silver Table

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE EXTERNAL TABLE IF NOT EXISTS ml_push.silver_l7_push_meetme_train_userid (
# MAGIC   broadcaster_id STRING,
# MAGIC   utc_day_of_week STRING,
# MAGIC   utc_hour STRING,
# MAGIC   network_user_id STRING,
# MAGIC   _processing_timestamp TIMESTAMP,
# MAGIC   open_flag BOOLEAN)
# MAGIC USING delta
# MAGIC PARTITIONED BY (_processing_timestamp)
# MAGIC LOCATION 'dbfs:/mnt/tmg-prod-datalake-outputs/push_data/silver_train_userid';

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE EXTERNAL TABLE IF NOT EXISTS ml_push.silver_l7_push_meetme_val_userid (
# MAGIC   broadcaster_id STRING,
# MAGIC   utc_day_of_week STRING,
# MAGIC   utc_hour STRING,
# MAGIC   network_user_id STRING,
# MAGIC   _processing_timestamp TIMESTAMP,
# MAGIC   open_flag BOOLEAN)
# MAGIC USING delta
# MAGIC PARTITIONED BY (_processing_timestamp)
# MAGIC LOCATION 'dbfs:/mnt/tmg-prod-datalake-outputs/push_data/silver_val_userid';

# COMMAND ----------

# use current timestamp to generate _processing_timestamp
spark.sql('''
  INSERT INTO TABLE ml_push.silver_l7_push_meetme_train_userid PARTITION (_processing_timestamp)
  SELECT 
      broadcaster_id,
      utc_day_of_week,
      utc_hour,
      network_user_id,
      CURRENT_TIMESTAMP as _processing_timestamp,
      open_flag
  from `{}`.{}
  '''.format(database_name,silver_train_tbl_name))

spark.sql('''
  INSERT INTO TABLE ml_push.silver_l7_push_meetme_val_userid PARTITION (_processing_timestamp)
  SELECT
      broadcaster_id,
      utc_day_of_week,
      utc_hour,
      network_user_id,
      CURRENT_TIMESTAMP as _processing_timestamp,
      open_flag
  from `{}`.{}
  '''.format(database_name,silver_val_tbl_name))

# COMMAND ----------

# MAGIC %sql
# MAGIC 
# MAGIC select * from ml_push.silver_l7_push_meetme_train_userid where _processing_timestamp = (select max(_processing_timestamp) from ml_push.silver_l7_push_meetme_train_userid)

# COMMAND ----------

# MAGIC %sql
# MAGIC 
# MAGIC select * from ml_push.silver_l7_push_meetme_val_userid where _processing_timestamp = (select max(_processing_timestamp) from ml_push.silver_l7_push_meetme_val_userid)

# COMMAND ----------

# MAGIC %md
# MAGIC Create AutoML model to help us automatically test different models and parameters and reduce time manually testing and tweaking ML models. 

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC ###AutoML Model

# COMMAND ----------

silver_train_df = spark.sql('''
select 
    broadcaster_id,
    utc_day_of_week,
    utc_hour,
    network_user_id,
    open_flag 
from ml_push.silver_l7_push_meetme_train_userid 
where _processing_timestamp = (select max(_processing_timestamp) from ml_push.silver_l7_push_meetme_train_userid)
'''
)

# COMMAND ----------

columns = [
    'open_flag',
    'utc_day_of_week',
    'utc_hour',
    'network_user_id',
    'broadcaster_id'
]

# COMMAND ----------

for c in columns:
    (silver_train_df
    .groupBy(c)
    .agg(F.mean(F.when(F.col('open_flag')==True, 1).otherwise(0)).alias('open rates'))
    .sort('open rates', ascending=False)
    .show())

# COMMAND ----------

import databricks.automl

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC data_dir is DBFS path used to store the training dataset. This path is visible to both driver and worker nodes. If empty, AutoML saves the training dataset as an MLflow artifact.

# COMMAND ----------

silver_train_df.show(10)

# COMMAND ----------

summary = databricks.automl.classify(silver_train_df, target_col='open_flag', primary_metric="f1", data_dir='dbfs:/automl/ml_push_userid', timeout_minutes=60)

# COMMAND ----------


