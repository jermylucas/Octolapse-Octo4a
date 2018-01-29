# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
import uuid
import time
import os
import sys
import traceback
import json
from pprint import pformat
import flask
from octoprint.server.util.flask import restricted_access, check_lastmodified, check_etag
from octoprint.server import admin_permission
import requests
import itertools
import shutil
import copy
import threading
import octoprint_octolapse.utility as utility
# Octoprint Imports
from octoprint.events import eventManager, Events # used to send messages to the web client for notifying it of new timelapses

# Octolapse imports
from octoprint_octolapse.settings import OctolapseSettings, Printer, Stabilization, Camera, Rendering, Snapshot, DebugProfile
from octoprint_octolapse.timelapse import Timelapse, TimelapseState
import octoprint_octolapse.camera as camera
from octoprint_octolapse.command import Commands
from octoprint_octolapse.utility import *
class OctolapsePlugin(	octoprint.plugin.SettingsPlugin,
						octoprint.plugin.AssetPlugin,
						octoprint.plugin.TemplatePlugin,
						octoprint.plugin.StartupPlugin,
						octoprint.plugin.EventHandlerPlugin,
						octoprint.plugin.BlueprintPlugin):
	TIMEOUT_DELAY = 1000

	def __init__(self):
		self.Settings = None
		self.Timelapse = None
		self.IsRenderingSynchronized = False
	#Blueprint Plugin Mixin Requests
	@octoprint.plugin.BlueprintPlugin.route("/downloadTimelapse/<filename>", methods=["GET"])
	@restricted_access
	@admin_permission.require(403)
	def downloadTimelapse(self, filename):
		return self.GetDownloadFileResponse(self.TimelapseFolderPath()+filename, filename)
	
	@octoprint.plugin.BlueprintPlugin.route("/downloadSettings", methods=["GET"])
	@restricted_access
	@admin_permission.require(403)
	def downloadSettings(self):
		return self.GetDownloadFileResponse(self.SettingsFilePath(), "Settings.json")

	@octoprint.plugin.BlueprintPlugin.route("/stopTimelapse", methods=["POST"])
	@restricted_access
	@admin_permission.require(403)
	def stopTimelapse(self):

		self.Timelapse.StopSnapshots()
		return json.dumps({'success':True}), 200, {'ContentType':'application/json'} 

	@octoprint.plugin.BlueprintPlugin.route("/saveMainSettings", methods=["POST"])
	@restricted_access
	@admin_permission.require(403)
	def saveMainSettings(self):
		requestValues = flask.request.get_json();
		clientId = requestValues["client_id"];
		self.Settings.is_octolapse_enabled = requestValues["is_octolapse_enabled"];
		self.Settings.show_navbar_icon = requestValues["show_navbar_icon"];
		self.Settings.show_extruder_state_changes = requestValues["show_extruder_state_changes"];
		self.Settings.show_position_state_changes = requestValues["show_position_state_changes"];
		# save the updated settings to a file.
		self.SaveSettings()
		mainSettings = self.Settings.GetMainSettingsDict()
		self.SendMainSettingsChangedMessage(mainSettings, clientId);
		data = {'success':True}
		data.update(mainSettings)
		return json.dumps(data), 200, {'ContentType':'application/json'} 
		
	@octoprint.plugin.BlueprintPlugin.route("/loadMainSettings", methods=["POST"])

	def loadMainSettings(self):
		data = {'success':True}
		data.update(self.Settings.GetMainSettingsDict())
		return json.dumps(data), 200, {'ContentType':'application/json'} 
	@octoprint.plugin.BlueprintPlugin.route("/loadStatus", methods=["POST"])
	def loadStatus(self):
		data = {'success':True}
		data.update(self.GetStatusDict())
		return json.dumps(data), 200, {'ContentType':'application/json'} 
	@octoprint.plugin.BlueprintPlugin.route("/addUpdateProfile", methods=["POST"])
	@restricted_access
	@admin_permission.require(403)
	def addUpdateProfile(self):
		requestValues = flask.request.get_json()
		profileType = requestValues["profileType"];
		profile = requestValues["profile"]
		client_id = requestValues["client_id"]
		updatedProfile = self.Settings.addUpdateProfile(profileType, profile)
		# save the updated settings to a file.
		self.SaveSettings()
		self.SendSettingsChangedMessage(client_id)
		return json.dumps(updatedProfile.ToDict()), 200, {'ContentType':'application/json'} ;

	@octoprint.plugin.BlueprintPlugin.route("/removeProfile", methods=["POST"])
	@restricted_access
	@admin_permission.require(403)
	def removeProfile(self):
		requestValues = flask.request.get_json();
		profileType = requestValues["profileType"];
		guid = requestValues["guid"]
		client_id = requestValues["client_id"]
		self.Settings.removeProfile(profileType, guid)
		# save the updated settings to a file.
		self.SaveSettings()
		self.SendSettingsChangedMessage(client_id)
		return json.dumps({'success':True}), 200, {'ContentType':'application/json'} 

	@octoprint.plugin.BlueprintPlugin.route("/setCurrentProfile", methods=["POST"])
	@restricted_access
	@admin_permission.require(403)
	def setCurrentProfile(self):
		requestValues = flask.request.get_json();
		profileType = requestValues["profileType"];
		guid = requestValues["guid"]
		client_id = requestValues["client_id"]
		self.Settings.setCurrentProfile(profileType, guid)
		self.SaveSettings()
		self.SendSettingsChangedMessage(client_id)
		return json.dumps({'success':True, 'guid':requestValues["guid"]}), 200, {'ContentType':'application/json'} 

	@octoprint.plugin.BlueprintPlugin.route("/restoreDefaults", methods=["POST"])
	@restricted_access
	@admin_permission.require(403)
	def restoreDefaults(self):
		requestValues = flask.request.get_json();
		client_id = requestValues["client_id"]
		self.LoadSettings(forceDefaults = True)
		data = {'success':True}
		data.update(self.Settings.ToDict())
		self.SendSettingsChangedMessage(client_id)
		return json.dumps(data), 200, {'ContentType':'application/json'} ;

	@octoprint.plugin.BlueprintPlugin.route("/loadSettings", methods=["POST"])
	@restricted_access
	@admin_permission.require(403)
	def loadSettings(self):
		data = {'success':True}
		data.update(self.Settings.ToDict())
		return json.dumps(data), 200, {'ContentType':'application/json'} ;
	
	@octoprint.plugin.BlueprintPlugin.route("/applyCameraSettings", methods=["POST"])
	@restricted_access
	@admin_permission.require(403)
	def applyCameraSettingsRequest(self):
		requestValues = flask.request.get_json()
		profile = requestValues["profile"]
		cameraProfile = Camera(profile)
		self.ApplyCameraSettings(cameraProfile)
		
		return json.dumps({'success':True}, 200, {'ContentType':'application/json'} )

	@octoprint.plugin.BlueprintPlugin.route("/testCamera", methods=["POST"])
	@restricted_access
	@admin_permission.require(403)
	def testCamera(self):
		requestValues = flask.request.get_json()
		profile = requestValues["profile"]
		cameraProfile = Camera(profile)
		results = camera.TestCamera(cameraProfile)
		if(results[0]):
			return json.dumps({'success':True}), 200, {'ContentType':'application/json'}
		else:
			return json.dumps({'success':False,'error':results[1]}), 200, {'ContentType':'application/json'}

	# blueprint helpers
	def GetDownloadFileResponse(self, filePath, downloadFileName):
		if(os.path.isfile(filePath)):
			
			def single_chunk_generator(downloadFile):
				while True:
					chunk = downloadFile.read(1024)
					if(not chunk):
						break
					yield chunk

			downloadFile = open(filePath, 'rb')
			response = flask.Response(flask.stream_with_context(single_chunk_generator(downloadFile)))
			response.headers.set('Content-Disposition', 'attachment', filename=downloadFileName)
			response.headers.set('Content-Type', 'application/octet-stream')
			return response
		return json.dumps({'success':False}), 404, {'ContentType':'application/json'}
	def ApplyCameraSettings(self, cameraProfile):
		cameraControl = camera.CameraControl(cameraProfile, self.OnCameraSettingsSuccess, self.OnCameraSettingsFail, self.OnCameraSettingsCompelted)
		cameraControl.ApplySettings()
	def TimelapseFolderPath(self):
		return utility.GetRenderingDirectoryFromDataDirectory(self.get_plugin_data_folder())

	def DefaultSettingsFilePath(self):
		return "{0}{1}data{1}settings_default.json".format(self._basefolder,os.sep)
	def SettingsFilePath(self):
		return "{0}{1}settings.json".format(self.get_plugin_data_folder(),os.sep)

	def LogFilePath(self):
		return self._settings.get_plugin_logfile_path();
	def LoadSettings(self,forceDefaults = False):
		try:
			# if the settings file does not exist, create one from the default settings
			
			createNewSettings = False

			if(not os.path.isfile(self.SettingsFilePath()) or forceDefaults):
				# create new settings from default setting file
				with open(self.DefaultSettingsFilePath()) as defaultSettingsJson:
					data = json.load(defaultSettingsJson);
					# if a settings file does not exist, create one ??
					self.Settings = OctolapseSettings(self.LogFilePath(), data);
				self.Settings.CurrentDebugProfile().LogSettingsLoad("Creating new settings file from defaults.")			
				createNewSettings = True
			else:
				# Load settings from file
				if(self.Settings is not None):
					self.Settings.CurrentDebugProfile().LogSettingsLoad("Loading existings settings file from: {0}.".format(self.SettingsFilePath()))
				else:
					self._logger.info("Loading existing settings file from: {0}.".format(self.SettingsFilePath()))
				with open(self.SettingsFilePath()) as settingsJson:
					data = json.load(settingsJson);
				if(self.Settings == None):
					#  create a new settings object
					self.Settings = OctolapseSettings(self.LogFilePath(), data);
					self.Settings.CurrentDebugProfile().LogSettingsLoad("Settings loaded.  Created new settings object: {0}.".format(data))			
				else:
					# update an existing settings object
					self.Settings.CurrentDebugProfile().LogSettingsLoad("Settings loaded.  Updating existing settings object: {0}.".format(data))			
					self.Settings.Update(data)	
			# Extract any settings from octoprint that would be useful to our users.
			self.CopyOctoprintDefaultSettings(applyToCurrentProfiles=createNewSettings)

			if(createNewSettings):
				# No file existed, so we must have created default settings.  Save them!
				self.SaveSettings()
			return self.Settings.ToDict();
		except TypeError,e:
			self._logger.exception("Could not load octolapse settings.  Details: {0}".format(e))

	def CopyOctoprintDefaultSettings(self, applyToCurrentProfiles = False):
		try:
			# move some octoprint defaults if they exist for the webcam
			# specifically the address, the bitrate and the ffmpeg directory.
			# Attempt to get the camera address and snapshot template from Octoprint settings
			snapshotUrl = self._settings.settings.get(["webcam","snapshot"])
			from urlparse import urlparse
			# we are doing some templating so we have to try to separate the
			# camera base address from the querystring.  This will probably not work
			# for all cameras.
			try:
				o = urlparse(snapshotUrl)
				cameraAddress = o.scheme + "://" + o.netloc + o.path
				self.Settings.CurrentDebugProfile().LogSettingsLoad("Setting octolapse camera address to {0}.".format(cameraAddress))
				snapshotAction = urlparse(snapshotUrl).query
				snapshotRequestTemplate = "{camera_address}?" + snapshotAction;
				self.Settings.CurrentDebugProfile().LogSettingsLoad("Setting octolapse camera snapshot template to {0}.".format(snapshotRequestTemplate))
				self.Settings.DefaultCamera.address = cameraAddress
				self.Settings.DefaultCamera.snapshot_request_template = snapshotRequestTemplate
				if(applyToCurrentProfiles):
					for profile in self.Settings.cameras.values():
						profile.address = cameraAddress;
						profile.snapshot_request_template = snapshotRequestTemplate
			except TypeError, e:
				self.Settings.CurrentDebugProfile().LogException(e)

			bitrate = self._settings.settings.get(["webcam","bitrate"])
			self.Settings.DefaultRendering.bitrate = bitrate
			if(applyToCurrentProfiles):
				for profile in self.Settings.renderings.values():
					profile.bitrate = bitrate
		except Exception, e:
			if(self.Settings is not None):
				self.Settings.CurrentDebugProfile().LogException(e)
			else:
				self._logger.exception(e)
	def SaveSettings(self):
		# Save setting from file
		settings = self.Settings.ToDict();
		self.Settings.CurrentDebugProfile().LogSettingsSave("Saving Settings.".format(settings))
		
		with open(self.SettingsFilePath(), 'w') as outfile:
			json.dump(settings, outfile)
		self.Settings.CurrentDebugProfile().LogSettingsSave("Settings saved: {0}".format(settings))
		return None
	#def on_settings_initialized(self):
	
	def CurrentPrinterProfile(self):
		"""Get the plugin's current printer profile"""
		return self._printer_profile_manager.get_current()

	## EVENTS
	#########
	def get_settings_defaults(self):
		return dict(load=None)

	def on_settings_load(self):
		settingsDict = None
		try:
			octoprint.plugin.SettingsPlugin.on_settings_load(self)
			settingsDict = self.Settings.ToDict()
		except Exception, e:
			if(self.Settings is not None):
				self.Settings.CurrentDebugProfile().LogException(e)
			else:
				self._logger.exception(e)
		
		return settingsDict
	def GetStatusDict(self):
		isTimelapseActive = False
		snapshotCount = 0
		secondsAddedByOctolapse = 0
		isTakingSnapshot = False
		isRendering = False
		timelapseState = TimelapseState.Idle
		positionState = None

		if(self.Timelapse is not None and self.Timelapse.State != TimelapseState.Idle):
			snapshotCount = self.Timelapse.SnapshotCount
			secondsAddedByOctolapse = self.Timelapse.SecondsAddedByOctolapse
			isTimelapseActive = True
			isRendering = self.Timelapse.IsRendering
			isTakingSnapshot = self.Timelapse.State == TimelapseState.TakingSnapshot
			timelapseState = self.Timelapse.State
			positionState = self.Timelapse.Position.ToDict()
		return {
			'snapshot_count': snapshotCount,
			'seconds_added_by_octolapse' : secondsAddedByOctolapse,
			'is_timelapse_active' : isTimelapseActive,
			'is_taking_snapshot' : isTakingSnapshot,
			'is_rdendering' : isRendering,
			'is_octolapse_enabled' : self.Settings.is_octolapse_enabled if self.Settings is not None else False,
			'show_navbar_icon' : self.Settings.show_navbar_icon if self.Settings is not None else False,
			'show_position_state_changes' : self.Settings.show_position_state_changes if self.Settings is not None else False,
			'show_extruder_state_changes' : self.Settings.show_extruder_state_changes if self.Settings is not None else False,
			'state' : timelapseState
		}

	def get_template_configs(self):
		self._logger.info("Octolapse - is loading template configurations.")
		return [dict(type="settings", custom_bindings=True)]

	def on_after_startup(self):
		try:
			self.LoadSettings()
			# create our initial timelapse object
			# create our timelapse object

			self.Timelapse = Timelapse(self.Settings
							  , self.get_plugin_data_folder()
							  , self._settings.getBaseFolder("timelapse")
							  ,onRenderStart = self.OnRenderStart
							  , onRenderComplete = None # I don't think we need this
							  , onRenderFail = self.OnRenderFail
							  , onRenderSynchronizeFail = self.OnRenderSynchronizeFail
							  , onRenderSynchronizeComplete = self.OnRenderSynchronizeComplete
							  , onRenderEnd = self.OnRenderEnd
							  
							  , onSnapshotStart = self.OnSnapshotStart
							  , onSnapshotEnd = self.OnSnapshotEnd
							  , onTimelapseStopping = self.OnTimelapseStopping
							  , onTimelapseStopped = self.OnTimelapseStopped
							  , onStateChanged = self.OnPositionExtruderStateChanged)
			self.Settings.CurrentDebugProfile().LogInfo("Octolapse - loaded and active.")
		except Exception, e:
			if(self.Settings is not None):
				self.Settings.CurrentDebugProfile().LogException(e)
			else:
				self._logger.exception(e)
				
	# Event Mixin Handler
	
	def on_event(self, event, payload):
		# If we haven't loaded our settings yet, return.
		if (self.Settings is None):
			return
		try:
			self.Settings.CurrentDebugProfile().LogPrintStateChange("Printer event received:{0}.".format(event))
			# for printing events use Printer State Change, because it gets sent before Print_Started
			if(event == Events.PRINT_STARTED):
				#eventId = self._printer.get_state_id()
				self.Settings.CurrentDebugProfile().LogPrintStateChange("State Change:{0}.".format(event))
				origin=payload["origin"]
				self.OnPrintStart(origin)
			elif(self.Timelapse == None):
				self.Settings.CurrentDebugProfile().LogPrintStateChange("No timelapse object exists and this is not a print start event, exiting.")
				return
			elif (event == Events.PRINT_PAUSED):
				self.OnPrintPause() # regular pause
			elif (event == Events.HOME):
				self.Settings.CurrentDebugProfile().LogPrintStateChange("homing to payload:{0}.".format(event))
			elif (event == Events.PRINT_RESUMED):
				self.OnPrintResumed()
			elif (event == Events.PRINT_FAILED):
				self.OnPrintFailed()
			elif (event == Events.PRINT_CANCELLED):
				self.OnPrintCancelled()
			elif (event == Events.PRINT_DONE):
				self.OnPrintCompleted()
			elif(event == Events.POSITION_UPDATE):
				self.Timelapse.PositionReceived(payload)
		except Exception as e:
			if(self.Settings is not None):
				self.Settings.CurrentDebugProfile().LogException(e)
			else:
				self._logger.exception(e)
	
	def OnPrintResumed(self):
		self.Settings.CurrentDebugProfile().LogPrintStateChange("Print Resumed.")
		self.Timelapse.PrintResumed()
	def OnPrintPause(self):
		self.Timelapse.PrintPaused()

	def OnPrintStart(self,origin):
		if(not self.Settings.is_octolapse_enabled):
			self.Settings.CurrentDebugProfile().LogPrintStateChange("Octolapse is disabled.")
			return

		if(self.Timelapse.State != TimelapseState.Idle):
			self.Settings.CurrentDebugProfile().LogPrintStateChange("Octolapse is not idling.  CurrentState:{0}".format(self.Timelapse.State))
			return 

		if(origin != "local"):
			self.SendPopupMessage("Unable to start octolapse when printing from SD the card.")
			self.Settings.CurrentDebugProfile().LogPrintStateChange("Octolapse cannot start the timelapse when printing from SD.  Origin:{0}".format(origin))
			return
		
		result = self.StartTimelapse()
		if(not result["success"]):
			self.Settings.CurrentDebugProfile().LogPrintStateChange("Unable to start the timelapse. Error:{0}".format(result["error"]))
			return

		self.Settings.CurrentDebugProfile().LogPrintStateChange("Print Started - Timelapse Started.")
		if(self.Settings.CurrentCamera().apply_settings_before_print):
			self.ApplyCameraSettings(self.Settings.CurrentCamera())
	
	def StartTimelapse(self):
		
		ffmpegPath = ""
		try:
			ffmpegPath = self._settings.settings.get(["webcam","ffmpeg"])
			if(self.Settings.CurrentRendering().enabled and ffmpegPath == ""):
				self.Settings.CurrentDebugProfile().LogError("A timelapse was started, but there is no ffmpeg path set!")
				return {'success':False, 'error':"No ffmpeg path is set.  Please configure this setting within the Octoprint settings pages located at Features->Webcam & Timelapse under Timelapse Recordings->Path to FFMPEG."}
		except Exception, e:
			if(self.Settings is not None):
				self.Settings.CurrentDebugProfile().LogException(e)
			else:
				self._logger.exception(e)
			return {'success':False, 'error':"An exception occurred while trying to acquire the ffmpeg path from Octoprint.  Please configure this setting within the Octoprint settings pages located at Features->Webcam & Timelapse under Timelapse Recordings->Path to FFMPEG."}
		if(not os.path.isfile(ffmpegPath)):
			# todo:  throw some kind of exception
			return {'success':False, 'error':"The ffmpeg {0} does not exist.  Please configure this setting within the Octoprint settings pages located at Features->Webcam & Timelapse under Timelapse Recordings->Path to FFMPEG.".format(ffmpegPath)}
		g90InfluencesExtruder = False
		try:
			g90InfluencesExtruder = self._settings.settings.get(["feature","g90InfluencesExtruder"])
			
		except Exception, e:
			if(self.Settings is not None):
				self.Settings.CurrentDebugProfile().LogException(e)
			else:
				self._logger.exception(e)
		self.Timelapse.StartTimelapse(self._printer, self._printer_profile_manager.get_current(), ffmpegPath,g90InfluencesExtruder)
		self.OnTimelapseStart()
		return {'success':True}

	def SendPopupMessage(self, msg):
		self.SendPluginMessage("popup", msg)

	def SendMainSettingsChangedMessage(self,mainSettings, clientId):
		data = {
			"type":"main-settings-changed"
			}
		data.update(mainSettings)
		self._plugin_manager.send_plugin_message(self._identifier, data)

	def SendStateChangedMessage(self, state):
		
		data = {
			"type":"state-changed"
			}
		data.update(state)
		self._plugin_manager.send_plugin_message(self._identifier, data)
	

	def SendSettingsChangedMessage(self,client_id):
		data = {
			"type":"settings-changed"
			,"client_id" : client_id
			}
		data.update(self.Settings.ToDict())
		self._plugin_manager.send_plugin_message(self._identifier, data)
		
	def SendStatusChangedMessage(self, client_id):
		data = {
			"type" : "status-changed",
			"client_id" : client_id
			}
		data.update(self.GetStatusDict())
		self._plugin_manager.send_plugin_message(self._identifier, data)

	def SendPluginMessage(self, type, msg):
		self._plugin_manager.send_plugin_message(self._identifier, dict(type=type, msg=msg))
	def SendSnapshotCompleteMessage(self, success, error, snapshotCount, secondsAdded):
		self._plugin_manager.send_plugin_message(
			self._identifier,
			dict(type="snapshot-complete",
				msg="Octolapse has taken a snapshot."
				, success = success
				, error = error 
				, snapshot_count = snapshotCount
				, seconds_added_by_octolapse = secondsAdded))
	def SendRenderStartMessage(self, msg, snapshotCount, secondsAdded):
		self._plugin_manager.send_plugin_message(self._identifier, dict(type="render-start", msg=msg, snapshot_count = snapshotCount, seconds_added_by_octolapse = secondsAdded))
	def SendRenderEndMessage(self,success):
		self._plugin_manager.send_plugin_message(self._identifier, dict(type="render-end", msg="Octolapse is finished rendering a timelapse.", is_synchronized = self.IsRenderingSynchronized, success = success))
	def OnTimelapseStart(self):
		self.SendPluginMessage("timelapse-start", "Octolapse is starting.")
	def OnTimelapseComplete(self):
		self.SendPluginMessage("timelapse-complete", "Octolapse is completing.")
	def OnSnapshotStart(self):
		self.SendPluginMessage("snapshot-start", "Octolapse is taking a snapshot.")
	def OnSnapshotEnd(self, *args, **kwargs):
		payload = args[0]
		success = payload["success"]
		error = payload["error"]
		snapshot_count = payload["snapshot_count"]
		seconds_added_by_octolapse = payload["seconds_added_by_octolapse"]
		self.SendSnapshotCompleteMessage(success, error, snapshot_count, seconds_added_by_octolapse)

	def OnCameraSettingsSuccess(self, *args, **kwargs):
		settingValue = args[0]
		settingName = args[1]
		template = args[2]
		self.Settings.CurrentDebugProfile().LogCameraSettingsApply("Camera Settings - Successfully applied {0} to the {1} setting.  Template:{2}".format(settingValue,settingName,template ))

	def OnCameraSettingsFail(self, *args, **kwargs):
		settingValue = args[0]
		settingName = args[1]
		template = args[2]
		errorMessage = args[3]
		self.Settings.CurrentDebugProfile().LogCameraSettingsApply("Camera Settings - Unable to apply {0} to the {1} settings!  Template:{2}, Details:{3}".format(settingValue,settingName,template,errorMessage))
		
	def OnCameraSettingsCompelted(self, *args, **kwargs):
		self.Settings.CurrentDebugProfile().LogCameraSettingsApply("Camera Settings - Completed")

	def OnPrintFailed(self):
		self.EndTimelapse()
		self.Settings.CurrentDebugProfile().LogPrintStateChange("Print Failed.")
	def OnPrintCancelled(self):
		self.EndTimelapse()
		self.Settings.CurrentDebugProfile().LogPrintStateChange("Print Cancelled.")
	def OnPrintCompleted(self):
		self.EndTimelapse()
		self.Settings.CurrentDebugProfile().LogPrintStateChange("Print Completed.")
	def OnPrintEnd(self):
		# tell the timelapse that the print ended.
		self.EndTimelapse()
		self.Settings.CurrentDebugProfile().LogPrintStateChange("Print Ended.");

	def EndTimelapse(self):
		if(self.Timelapse is not None):
			self.Timelapse.EndTimelapse()
			self.OnTimelapseComplete()

	def OnTimelapseStopping(self):
		self.SendPluginMessage("timelapse-stopping", "Waiting for a snapshot to complete before stopping the timelapse.")

	def OnTimelapseStopped(self):
		self.SendPluginMessage("timelapse-stopped", "Octolapse has been stopped for the remainder of the print.")

	def GcodeQueuing(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
		try:
			# only handle commands sent while printing
			if(self.Timelapse is not None and self.Timelapse.IsTimelapseActive()):
				return self.Timelapse.GcodeQueuing(comm_instance,phase,cmd,cmd_type,gcode,args,kwargs)

		except Exception, e:
			if(self.Settings is not None):
				self.Settings.CurrentDebugProfile().LogException(e)
			else:
				self._logger.exception(e)

	def GcodeSent(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
		try:
			if(self.Timelapse is not None and self.Timelapse.IsTimelapseActive()):
				self.Timelapse.GcodeSent(comm_instance,phase,cmd,cmd_type, gcode, args, kwargs)
		except Exception, e:
			if(self.Settings is not None):
				self.Settings.CurrentDebugProfile().LogException(e)
			else:
				self._logger.exception(e)

	def OnPositionExtruderStateChanged(self, *args, **kwargs):
		stateChangeDict = args[0]
		self.SendStateChangedMessage(stateChangeDict)

	
	def OnRenderStart(self, *args, **kwargs):
		"""Called when a timelapse has started being rendered.  Calls any callbacks OnRenderStart callback set in the constructor."""
		payload = args[0]
		# Set a flag marking that we have not yet synchronized with the default Octoprint plugin, in case we do this later.
		self.IsRenderingSynchronized = False
		#Generate a notification message
		timeAddedMessage = "Octolapse has started rending your timelapse."
		willSyncMessage = ""
		if (payload["WillSync"]):
			willSyncMessage = "  This timelapse will synchronized with the default timelapse module, and will be available in the 'Timelapse' tab after rendering is complete.    Please see the Octolapse advanced rendering settings for details."
		else:
			willSyncMessage = "  This timelapse will NOT be synchronized with the default timelapse module.  Please see the Octolapse advanced rendering settings for details."

		message = "{0}{1}".format(timeAddedMessage,willSyncMessage)
		# send a message to the client
		self.SendRenderStartMessage(message, payload["SnapshotCount"], payload["SnapshotTimeSeconds"])


	def OnRenderFail(self, *args, **kwargs):
		"""Called after a timelapse rendering attempt has failed.  Calls any callbacks onMovieFailed callback set in the constructor."""
		payload = args[0]
		# Octoprint Event Manager Code
		self.SendPluginMessage("render-failed", "Octolapse has failed to render a timelapse.  Reason:{0}".format(payload["reason"]))
	def OnRenderSynchronizeFail(self, *args, **kwargs):
		"""Called when a synchronization attempt with the default app fails."""
		payload = args[0]
		# Octoprint Event Manager Code
		self.SendPluginMessage("synchronize-failed", "Octolapse has failed to syncronize the default timelapse plugin.  Reason:{0}".format(payload["reason"]))

	def OnRenderSynchronizeComplete(self, *args, **kwargs):
		"""Called when a synchronization attempt goes well!  Notifies Octoprint of the new timelapse!"""
		payload = args[0]
		# Octoprint Event Manager Code
		self.IsRenderingSynchronized = True
		eventManager().fire(Events.MOVIE_DONE, payload)

	def OnRenderEnd(self, *args, **kwargs):
		"""Called after all rendering and synchronization attemps are complete."""
		payload = args[0]
		self.SendRenderEndMessage(payload["success"])

	
	##~~ AssetPlugin mixin
	def get_assets(self):
		self._logger.info("Octolapse is loading assets.")
		# Define your plugin's asset files to automatically include in the
		# core UI here.
		return dict(
			js = [
				"js/jquery.validate.min.js"
				,"js/octolapse.js"
				,"js/octolapse.settings.js"
				,"js/octolapse.settings.main.js"
				,"js/octolapse.profiles.js"
				,"js/octolapse.profiles.printer.js"
				,"js/octolapse.profiles.stabilization.js"
				,"js/octolapse.profiles.snapshot.js"
				,"js/octolapse.profiles.rendering.js"
				,"js/octolapse.profiles.camera.js"
				,"js/octolapse.profiles.debug.js"
				,"js/octolapse.status.js"
			],
			css = ["css/octolapse.css"],
			less = ["less/octolapse.less"])

	##~~ Softwareupdate hook
	def get_update_information(self):
		# Define the configuration for your plugin to use with the Software Update
		# Plugin here.  See
		# https://github.com/foosel/OctoPrint/wiki/Plugin:-Software-Update
		# for details.
		self._logger.info("Octolapse is getting update information.")
		return dict(octolapse = dict(displayName="Octolapse Plugin",
				displayVersion=self._plugin_version,
				# version check: github repository
				type="github_release",
				user="FormerLurker",
				repo="Octolapse",
				current=self._plugin_version,
				# update method: pip
				pip="https://github.com/FormerLurker/Octolapse/archive/{target_version}.zip"))

# If you want your plugin to be registered within OctoPrint under a different
# name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here.  Same goes for the
# other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties.  See the
# documentation for that.
__plugin_name__ = "Octolapse Plugin"

def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = OctolapsePlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
		"octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.GcodeQueuing,
		"octoprint.comm.protocol.gcode.sent": __plugin_implementation__.GcodeSent
	}

