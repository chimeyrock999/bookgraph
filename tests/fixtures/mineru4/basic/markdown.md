## Designing Data Systems

A short book about reliable data systems.

## Chapter 1 Replication

## 1.1 Leaders and Followers

Replication keeps copies of the same data on several machines. A leader accepts writes and followers apply the change log.

![](images/page_1_image_4.jpg)

Figure 1-1. A leader and two followers.

## Chapter 2 Storage

## 2.1 Log-Structured Engines

Storage engines append records to a log and compact it later.

Indexes speed up reads at the cost of slower writes.