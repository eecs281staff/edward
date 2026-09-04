#!/usr/bin/env python3

import sys

import requests


def request(method, url, **kwargs):
    status = ''
    body = ''

    try:
        r = requests.request(method, url, **kwargs)
        status = r.status_code
        body = r.text

        if 200 <= r.status_code <= 299:
            return r
        else:
            raise Exception('Bad status code: ', status)

    except Exception as e:
        print('Request failed')
        print(f'Status: {status}')
        print(f'URL: {url}')
        print(f'Body: {body}')
        status = str(e);

        raise e

def main():
    ed_host, token = sys.argv[1:]

    r = request('GET', ed_host + '/user', headers={'Authorization': 'Bearer ' + token})
    u = r.json()

    r = request('GET', ed_host + '/courses/104375/analytics/discussion', headers={'Authorization': 'Bearer ' + token})

    print('Logged in as', u['user']['email'])

    print(r.json())

if __name__ == '__main__':
    main()
