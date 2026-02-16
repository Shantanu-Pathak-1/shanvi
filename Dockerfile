FROM python:3.11

WORKDIR /code

# Copy requirements and install
COPY ./requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

# Copy ALL files (app.py, index.html) to root
COPY . /code

# Run App
CMD ["gunicorn", "-b", "0.0.0.0:7860", "app:app"]