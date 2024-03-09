import requests
from dataclasses import dataclass
from requests.auth import HTTPBasicAuth
import logging
import os
import sys
from datetime import datetime, timedelta
import time

LOG_LEVEL = os.environ.get("LOGGING", "INFO").upper()

logging.basicConfig(
    stream=sys.stdout,
    level=LOG_LEVEL,
    style="{",
    format="{asctime} {levelname} {name} {threadName} : {message}",
)

lgr = logging.getLogger(__name__)


@dataclass
class MotionEvent:
    start = None
    end = None
    camera = None


# class Stream:
#     def __init__(self, sdk_url, mask):
#         self.sdk_url = sdk_url
#         self.mask = mask
#
#     def file_upload(self, video_path):
#         if self.mask:
#             data = dict(mask_id=self.mask)
#         else:
#             data = None
#
#         with open(video_path, "rb") as fp:
#             response = requests.post(self.sdk_url, files=dict(upload=fp), data=data)
#             if response.status_code < 200 or response.status_code > 300:
#                 logging.error(response.text)
#                 return None
#             else:
#                 return response.json()


class Salient:
    RETRY_LIMIT = 3

    def __init__(self, vms_api, username, password):
        self.vms_api = vms_api
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(username, password)
        self.time_format = "%Y-%m-%dT%H:%M:%SZ"  # "2023-10-29T14:00:00Z"

    def search_motion_events(self, cameras) -> list[MotionEvent]:
        """
        Retrieve motion events most recent last
        POST /events/search
        :return:
        """

        entities = self.get_enums()
        motion_start = None
        motion_end = None
        for entity in entities:
            if entity['entityType'] != 1:  # entityDescription is Camera
                continue
            else:
                for event in entity['supportEvents']:
                    if event['eventType'] == 10:
                        motion_start = 10
                    if event['eventType'] == 11:
                        motion_end = 11

                if motion_start and motion_end:
                    break

        if not motion_start or not motion_end:
            raise Exception('Events motion_start or motion_end unsupported')

        # TODO start with last 20 mins
        now = datetime.utcnow()
        time_ago = now - timedelta(minutes=10)

        start = time_ago.strftime(self.time_format)
        end = now.strftime(self.time_format)

        events = self.search(start, end, [motion_start, motion_end], cameras)

        motion_events = []
        # Group and create MotionEvent(s)
        motion_event = None
        for event in events:
            if event['type'] == motion_start:
                # Ensure no double motion start events # TODO support multiple cameras
                assert motion_event is None
                motion_event = MotionEvent()
                motion_event.start = event['time']
                # TODO motion_event.camera = event['camera']
                motion_event.camera = '6a194dd0-23c1-4b1a-a039-1070bdecbba1'

            elif event['type'] == motion_end and motion_event is not None:
                # Ensure no double motion start events # TODO support multiple cameras
                # TODO assert motion_event.camera == event['camera']
                motion_event.end = event['time']
                motion_events.append(motion_event)
                motion_event = None

        return motion_events

    def download_video(self, motion_event: MotionEvent):
        """
        To retrieve recorded video, you would use something like this:
        GET /cameras/1/videofilesdownload?accept=application/json&start=2017-01-30T16:50:28.843Z&stop=2017-01-30T16:53:29.847Z

        It is recommended to use that in conjunction with:
        GET /cameras/1/videofiles?accept=application/json&start=2017-01-30T00:00:00Z&stop=2017-01-30T23:59:59Z

        Video is downloaded as an AVI file. Other choices include AVI and AVI.
        :param motion_event:
        :return:
        """

        start = Salient.windows_to_unix_time(motion_event.start).strftime(self.time_format)  # '2017-01-30T00:00:00Z'
        stop = Salient.windows_to_unix_time(motion_event.end).strftime(self.time_format)  # '2017-01-30T23:59:59Z'
        # endpoint = f'/v1.0/cameras/{motion_event.camera}/videofiles?accept=application/json&start={start}&stop={stop}'
        # args = {
        #     'method': 'GET',
        #     'url': self.vms_api + endpoint,
        # }
        # res = self.salient_request(args)
        # lgr.info(res.text)

        endpoint = f'/v1.0/cameras/{motion_event.camera}/videofilesdownload?accept=application/json&start={start}&stop={stop}'
        args = {
            'method': 'GET',
            'url': self.vms_api + endpoint,
            'stream': True
        }
        response = self.salient_request(args)
        filename = "downloaded_video.avi"
        # Download the video content in chunks
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(1024):
                if chunk:  # filter out keep-alive new chunks
                    f.write(chunk)
        lgr.info(f"Downloaded AVI video: {filename}")
        return filename

    def process_video(self, video_file):
        """
        Process video_file through Stream and get back Results
        :param video_file:
        :return:
        """
        raise NotImplementedError

    def salient_request(self, args):
        # TODO retry server failures
        tries = 1
        while True:
            try:
                response = self.session.request(**args)
                lgr.debug(f'response: {response}')
                lgr.debug(f'response: {response.headers}')
                if response.status_code < 200 or response.status_code > 300:
                    if response.status_code == 429:
                        time.sleep(1)
                        tries += 1
                    else:
                        response.raise_for_status()
                return response
            except requests.exceptions.ConnectionError as e:
                lgr.error("Error Connecting:", exc_info=e)
                time.sleep(1)
                tries += 1
            except requests.exceptions.Timeout as e:
                lgr.error("Timeout Error:", exc_info=e)
                time.sleep(1)
                tries += 1

            if tries > Salient.RETRY_LIMIT:
                raise Exception('Retry Limit')

    def get_cameras(self):
        endpoint = '/v2.0/cameras'
        args = {
            'method': 'GET',
            'url': self.vms_api + endpoint,
        }
        response = self.salient_request(args)
        return response.json()['cameras']

    def camera_snapshot(self, id_or_guid):
        endpoint = f'/v1.0/cameras/{id_or_guid}/media?accept=image/jpeg'
        args = {
            'method': 'GET',
            'url': self.vms_api + endpoint,
        }
        response = self.salient_request(args)
        with open(f'camera-{id_or_guid}-snapshot.jpg', 'wb') as out_file:
            out_file.write(response.content)

    def send_events(self, results):
        lgr.debug(f"send_events CompleteView: {results}")
        events = []
        for result in results:
            camera_uid, source, description, timestamp = None  # TODO get from result
            event = {
                "entityType": 1,
                "eventType": 58,
                "eventDescription": f"Plate Detection [{description}]",
                "user": f"Platerecognizer({source})",
                "deviceGuid": camera_uid,
            }
            events.append(event)

        endpoint = "/v2.0/events"
        args = {
            'method': 'POST',
            'url': self.vms_api + endpoint,
            'json': {"events": events},

        }
        res = self.salient_request(args)

    def get_enums(self):
        """
        Retrieve supported event types
        return all non‐deprecated entity enums with their specific supported event types.
        GET /events/enums
        :return:
        """
        endpoint = '/v2.0/events/enums'
        args = {
            'method': 'GET',
            'url': self.vms_api + endpoint,
        }
        res = self.salient_request(args)
        return res.json().get('entities')

    def search(self, start, end, events, cameras=None):
        """
        1709763725
        133542367605500000

        query events
        POST /events/search
        :return:
        """
        endpoint = '/v2.0/events/search'
        log_events = {
            "startTimeUtc": start,
            "endTimeUtc": end,
            "events": events,
            "includeServerEvents": True,
            "maxResults": 20
        }
        if cameras is not None:
            log_events["cameras"] = cameras

        args = {
            'method': 'POST',
            'url': self.vms_api + endpoint,
            'json': {
                "logEvents": log_events
            }
        }
        res = self.salient_request(args)
        return res.json().get('events')

    @staticmethod
    def windows_to_unix_time(ft) -> datetime:
        """
        Convert Salient event time to datetime
        :param ft:
        :return:
        """
        epoch_diff = 116444736000000000
        rate_diff = 10000000
        ts = int((ft - epoch_diff) / rate_diff)
        return datetime.utcfromtimestamp(ts)


def main(username, password, vms):
    salient = Salient(vms, username, password)
    # stream = Stream('http://localhost:8081')

    # Retrieve available cameras to get GUIDs
    vms_cameras = salient.get_cameras()
    lgr.info(f'Cameras: {vms_cameras}')

    # Retrieve Snapshot From Camera
    camera_guid = '6a194dd0-23c1-4b1a-a039-1070bdecbba1'
    salient.camera_snapshot(camera_guid)

    last_motion_event = None
    # Periodically check for motion events
    while True:
        motion_events = salient.search_motion_events([camera_guid])
        for motion_event in motion_events:
            # skip processed
            if last_motion_event is not None and motion_event.end < last_motion_event.start:
                continue

            video = salient.download_video(motion_event)
            results = salient.process_video(video)
            salient.send_events(results)

            last_motion_event = motion_event


if __name__ == '__main__':
    u = 'admin'
    p = 'brian123'
    recording_server = 'http://192.168.122.66:4502'
    main(u, p, recording_server)
