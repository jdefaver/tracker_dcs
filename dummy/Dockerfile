FROM python:3

WORKDIR /usr/src/app

COPY ../trackerdcs/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ../trackerdcs .

CMD [ "python", "-m", "unittest",  "discover"]