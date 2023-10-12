BEGIN;
CREATE DATABASE private_database;
COMMIT;

BEGIN;
USE private_database;

CREATE TABLE diary_day(
id INT PRIMARY KEY AUTO_INCREMENT,
today_date DATE NOT NULL UNIQUE DEFAULT(CURDATE()),
fell_asleep TIME,
woke_up TIME,
focus TINYINT DEFAULT 5,
start_mood TINYINT DEFAULT 5,
end_mood TINYINT DEFAULT 5,
satisfaction TINYINT DEFAULT 5,

CHECK (focus BETWEEN 1 AND 10),
CHECK (start_mood BETWEEN 1 AND 10),
CHECK (end_mood BETWEEN 1 AND 10),
CHECK (satisfaction BETWEEN 1 AND 10)
);

CREATE TABLE comment_type(
id INT PRIMARY KEY AUTO_INCREMENT,
comment_type_name CHAR(30) NOT NULL UNIQUE
);

CREATE TABLE day_comment(
day_id INT NOT NULL,
comment_type_id INT NOT NULL,
comment_text TEXT(1000) NOT NULL,

PRIMARY KEY(day_id, comment_type_id),
FOREIGN KEY (day_id) REFERENCES diary_day(id),
FOREIGN KEY (comment_type_id) REFERENCES comment_type(id)
);

CREATE TABLE activit_category(
id INT PRIMARY KEY AUTO_INCREMENT,
category_name CHAR(30) NOT NULL UNIQUE
);

CREATE TABLE diary_activity(
id INT PRIMARY KEY AUTO_INCREMENT,
day_id INT NOT NULL,
activity_start TIME NOT NULL,
activity_end TIME NOT NULL,
category_id INT NOT NULL,
description VARCHAR(100),

FOREIGN KEY (day_id) REFERENCES diary_day(id),
FOREIGN KEY (category_id) REFERENCES activit_category(id)
);

CREATE TABLE diary_tag(
id INT PRIMARY KEY AUTO_INCREMENT,
tag_name CHAR(30) NOT NULL UNIQUE
);

CREATE TABLE tag_day(
day_id INT NOT NULL,
tag_id INT NOT NULL,

PRIMARY KEY(day_id, tag_id),
FOREIGN KEY (day_id) REFERENCES diary_day(id),
FOREIGN KEY (tag_id) REFERENCES diary_tag(id)
);

CREATE TABLE tag_activity(
activity_id INT NOT NULL,
tag_id INT NOT NULL,

PRIMARY KEY(activity_id, tag_id),
FOREIGN KEY (activity_id) REFERENCES diary_activity(id),
FOREIGN KEY (tag_id) REFERENCES diary_tag(id)
);

COMMIT;
