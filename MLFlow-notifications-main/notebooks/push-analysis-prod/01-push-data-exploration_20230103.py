# Databricks notebook source
# import libraries
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.functions import max as sparkMax
from pyspark.sql.types import IntegerType, StringType, BooleanType, DateType, DoubleType
from pyspark.sql.window import Window
from pyspark.ml.feature import StandardScaler, VectorAssembler, StringIndexer, OneHotEncoder, Normalizer
from pyspark.ml.classification import RandomForestClassifier, GBTClassifier, LogisticRegression
from pyspark.ml import Pipeline
from pyspark.ml.tuning import CrossValidator, ParamGridBuilder
from pyspark.ml.evaluation import MulticlassClassificationEvaluator
from pyspark.mllib.evaluation import BinaryClassificationMetrics as metric
from sklearn.metrics import roc_curve, auc
 
import time
import datetime
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib import pyplot as plt

# COMMAND ----------

# MAGIC %md 
# MAGIC ### Initial Database Setup

# COMMAND ----------

dbutils.credentials.showCurrentRole()

# COMMAND ----------

dbutils.fs.ls('/mnt')

# COMMAND ----------

# Move file from driver to DBFS
user = dbutils.notebook.entry_point.getDbutils().notebook().getContext().tags().apply('user')

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC ### Readin Push data

# COMMAND ----------

# bronze_df = spark.sql("select * from ml_push.l7_push_meetme_source_partitioned where calculated_time > CURRENT_TIMESTAMP - INTERVAL '1' HOUR")
# bronze_df = spark.sql("select * from ml_push.l7_push_meetme_source_partitioned where calculated_time = (select max(calculated_time) from ml_push.l7_push_meetme_source_partitioned)")
# bronze_df = spark.sql("select * from ml_push.l7_push_meetme_source_partitioned where calculated_time = '2022-10-31 19:20:06.243'")
# bronze_df = spark.sql("select * from ml_push.l7_push_meetme_source_partitioned where calculated_time = '2022-11-04 19:19:55.364'")

# bronze_df = spark.sql("select * from ml_push.temp_push_demographics")
bronze_df = spark.sql("select * from ml_push.temp_push_demographics_v2")

# COMMAND ----------

def shape(data):
    rows, cols = data.count(), len(data.columns)
    shape = (rows, cols)
    return shape

# COMMAND ----------

shape(bronze_df)

# COMMAND ----------

dbutils.data.summarize(bronze_df)

# COMMAND ----------

bronze_df.printSchema()

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC ### Missing Data

# COMMAND ----------

missing_cols = [
    'open_ts',
    'registration_ts',
    'age',
    'gender',
    'country',
    'locale'
]

# COMMAND ----------

bronze_df.filter(bronze_df.open_ts.isNull()).show(1, vertical=True)

# COMMAND ----------

bronze_df.filter(bronze_df.registration_ts.isNull()).show(1, vertical=True)

# COMMAND ----------

bronze_df.filter(bronze_df.age.isNull()).show(1, vertical=True)

# COMMAND ----------

bronze_df.filter(bronze_df.gender.isNull()).show(1, vertical=True)

# COMMAND ----------

bronze_df.filter(bronze_df.country.isNull()).show(1, vertical=True)

# COMMAND ----------

bronze_df.filter(bronze_df.locale.isNull()).show(1, vertical=True)

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC ### Derived Features

# COMMAND ----------

bronze_df = bronze_df.withColumn('cal_date', F.from_unixtime(F.col('send_ts')/1000).cast(DateType())) \
    .withColumn('cal_utc_day_of_week', F.dayofweek(F.from_unixtime(F.col('send_ts')/1000).cast(DateType()))) \
    .withColumn('cal_utc_hour', F.hour(F.from_unixtime(F.col('send_ts')/1000))) \
    .withColumn('broadcaster_id', F.concat(F.col('from_user_network'), F.lit(':user:'), F.col('from_user_id'))) \
    .withColumn('lang', F.substring(F.col('locale'), 1, 2)) \
    .withColumn('from_user_lang', F.substring(F.col('from_user_locale'), 1, 2))

# COMMAND ----------

bronze_df.count()

# COMMAND ----------

bronze_df.show(1, vertical=True)

# COMMAND ----------

def count_dist(df, field):
    return df.select(field).distinct().count()

def count_grp_dist(df, field):
    return df.groupBy(field).count().sort('count', ascending=False).show()

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC ### Network User and Broadcaster counts

# COMMAND ----------

count_dist(bronze_df, 'network_user_id')

# COMMAND ----------

count_grp_dist(bronze_df, 'network_user_id')

# COMMAND ----------

count_dist(bronze_df, 'broadcaster_id')

# COMMAND ----------

count_grp_dist(bronze_df, 'broadcaster_id')

# COMMAND ----------

# MAGIC %md
# MAGIC 
# MAGIC ### Categorical Features

# COMMAND ----------

categorical_features = [
'device_type',
'gender',
'age',
'country',
'locale',
'lang',
'from_user_age',
'from_user_gender',
'from_user_country',
'from_user_locale',
'from_user_lang',
'cal_utc_hour',
'cal_utc_day_of_week'
]

# COMMAND ----------

for c in categorical_features:
    bronze_df.groupBy(c).agg(
        F.count('network_user_id').alias('sends'),
        F.countDistinct('network_user_id').alias('recipients'),
        F.count('network_user_id')/F.countDistinct('network_user_id').alias('push per recipient'),
        F.sum('open_flag').alias('opens'),
        F.sum('open_flag')/F.count('network_user_id').alias('open %')
    ).show()

# COMMAND ----------

# MAGIC %md
# MAGIC ### Numerical Features

# COMMAND ----------

numeric_features = [
    'broadcast_search_count_24h',
    'search_user_count_24h',
    'search_match_count_24h',
    'view_count_24h',
    'view_end_count_24h',
    'gift_count_24h',
]

# COMMAND ----------

for c in numeric_features:
    bronze_df.groupBy('open_flag').agg(
    F.avg(c).alias(c + '_avg'),
    F.min(c).alias(c + '_min'),
    F.max(c).alias(c + '_max'),
    F.percentile_approx(c, 0.25).alias(c + '_q25'),
    F.percentile_approx(c, 0.5).alias(c + '_median'),
    F.percentile_approx(c, 0.75).alias(c + '_q75')
).show()

# COMMAND ----------


