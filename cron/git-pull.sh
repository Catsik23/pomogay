#!/bin/bash
echo "$(date): git pull" >> /var/log/pomogay-deploy.log
cd /opt/pomogay && git pull >> /var/log/pomogay-deploy.log 2>&1
