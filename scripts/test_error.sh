#!/bin/bash
for i in 1 2
do
    curl  -sL -w "%{http_code}\n" -o /dev/null "http://localhost:8000/notexist"
done